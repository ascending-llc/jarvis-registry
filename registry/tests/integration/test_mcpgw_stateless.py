"""Integration coverage for the stateless MCP gateway.

Each "pod" is a full app built by the production ``create_app`` (real auth middleware, the mounted MCP app
wrapped in ``ClientInfoCaptureMiddleware``) with its own FastMCP session manager. Pods share only a fake
Redis. A request with no ``Mcp-Session-Id`` affinity is served by any pod, a stale session id is ignored
rather than 404'd, and the capability a client declares in ``initialize`` on one pod decides whether
another pod answers a ``tools/call`` with a ``-32042`` URL elicitation or the plain-text fallback.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI
from mcp.types import ClientCapabilities, ElicitationCapability, Implementation, UrlElicitationCapability
from starlette.testclient import TestClient

from registry.app_factory import create_app
from registry.core.config import settings
from registry.mcpgw import create_gateway_mcp_app
from registry.mcpgw.core.client_info_store import ClientInfoStore
from registry.mcpgw.tools import server as server_tools
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

pytestmark = [pytest.mark.integration]

USER_ID = "507f1f77bcf86cd799439011"
CLIENT_ID = "mcp-client-abc"  # DCR id -> cached under (user, client) without a jti
SERVER_ID = str(PydanticObjectId())
_MCP_PATH = "/proxy/mcpgw/mcp"
_MCP_ACCEPT = "application/json, text/event-stream"
_URL_ELICITATION_REQUIRED = -32042


@pytest.fixture(autouse=True)
def _disable_dns_rebinding_protection(monkeypatch: pytest.MonkeyPatch):
    """TestClient sends Host: testserver, which the gateway's DNS-rebinding guard would reject (421).
    That guard is orthogonal to statelessness, so turn it off for these transport-level tests."""
    monkeypatch.setattr(settings, "mcpgw_enable_dns_rebinding_protection", False)


@pytest.fixture(autouse=True)
def _no_downstream_auth_headers(monkeypatch: pytest.MonkeyPatch):
    """Downstream auth-header building is out of scope here; pass the base headers through."""

    async def _passthrough(*, additional_headers: dict[str, str], **_kwargs) -> dict[str, str]:
        return dict(additional_headers)

    monkeypatch.setattr(server_tools, "build_authenticated_headers", _passthrough)


class _FakeRedis:
    """Minimal Redis stand-in shared by every pod."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    def get(self, key: str) -> str | None:
        return self.store.get(key)


def _server_doc() -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(SERVER_ID),
        path="/github",
        serverName="github",
        mcp_auth_mode=server_tools.McpAuthMode.NONE,
        config={"requiresInit": False, "type": "streamable-http", "url": "https://downstream.example/mcp"},
        is_tool_disabled=lambda _name: False,
    )


def _container_stub(
    redis: _FakeRedis,
    *,
    has_consent: bool,
    proxy_client: httpx.AsyncClient | None = None,
) -> SimpleNamespace:
    """A per-pod container: its own ClientInfoStore and mocks, sharing only the Redis stand-in."""
    server_service = MagicMock()
    server_service.get_server_by_id = AsyncMock(return_value=_server_doc())
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value={SERVER_ID})
    consent_store = MagicMock()
    consent_store.has_server_consent.return_value = has_consent
    return SimpleNamespace(
        mcpgw_proxy_client=proxy_client or MagicMock(),
        a2a_client_registry=MagicMock(),
        server_service=server_service,
        mcp_server_repo=MagicMock(),
        a2a_agent_repo=MagicMock(),
        search_service=MagicMock(),
        mcp_client_service=MagicMock(),
        oauth_service=MagicMock(),
        client_info_store=ClientInfoStore(redis, settings.redis_key_prefix, settings.jwt_token_config),
        redis_client=MagicMock(),
        acl_service=acl_service,
        consent_store=consent_store,
        pending_consent_store=MagicMock(),
    )


def _make_pod(container: SimpleNamespace) -> FastAPI:
    """Build one pod the way main.py does: production create_app, with the MCP session manager run in the
    app lifespan (Starlette does not run a mounted app's own lifespan)."""
    gateway_mcp_app = create_gateway_mcp_app(container_provider=lambda: container)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with gateway_mcp_app.session_manager.run():
            yield

    return create_app(lifespan=lifespan, gateway_mcp_app=gateway_mcp_app, container_provider=lambda: container)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = mint_managed_agent_token(
        settings.jwt_token_config,
        subject=USER_ID,
        client_id=CLIENT_ID,
        requested_scopes="mcp-proxy-ops",
        expires_in_seconds=3600,
        extra_claims={"preferred_username": "alice"},
    )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": _MCP_ACCEPT}


def _pod_client(container: SimpleNamespace) -> TestClient:
    return TestClient(_make_pod(container), base_url="http://localhost")


def _post(client: TestClient, body: dict, headers: dict[str, str]) -> list[dict]:
    resp = client.post(_MCP_PATH, content=json.dumps(body).encode(), headers=headers)
    assert resp.status_code == 200, resp.text
    assert "mcp-session-id" not in resp.headers
    return _sse_messages(resp.text)


def _sse_messages(resp_text: str) -> list[dict]:
    """Every JSON-RPC message on an SSE response body, in order.

    A complete `data:` event carrying the final result proves the streaming response ran to completion — the
    guard against the capture middleware's receive replay cancelling the SSE stream before its result is written.
    """
    return [json.loads(line[len("data:") :].strip()) for line in resp_text.splitlines() if line.startswith("data:")]


def _initialize(client_name: str, *, url_elicitation: bool) -> dict:
    elicitation = ElicitationCapability(url=UrlElicitationCapability()) if url_elicitation else None
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": ClientCapabilities(elicitation=elicitation).model_dump(mode="json", exclude_none=True),
            "clientInfo": Implementation(name=client_name, version="1.0").model_dump(mode="json"),
        },
    }


def _execute_tool(request_id: int = 2, *, progress_token: str | None = None) -> dict:
    params: dict = {
        "name": "execute_tool",
        "arguments": {"tool_name": "tavily_search", "server_id": SERVER_ID, "arguments": {}},
    }
    if progress_token is not None:
        params["_meta"] = {"progressToken": progress_token}
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


def _tools_list() -> dict:
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _initialize_on_a_then_call_on_b(auth_headers: dict[str, str], client_name: str, *, url_elicitation: bool) -> dict:
    redis = _FakeRedis()
    with _pod_client(_container_stub(redis, has_consent=False)) as pod_a:
        init_msgs = _post(pod_a, _initialize(client_name, url_elicitation=url_elicitation), auth_headers)
    assert "result" in init_msgs[-1], "initialize SSE body was truncated/cancelled"

    with _pod_client(_container_stub(redis, has_consent=False)) as pod_b:
        call_msgs = _post(pod_b, _execute_tool(), auth_headers)
    return call_msgs[-1]


def test_url_elicitation_capability_declared_on_one_pod_yields_32042_on_another(auth_headers):
    """A client that declared URL elicitation on pod A gets a -32042 error from pod B's consent gate."""
    msg = _initialize_on_a_then_call_on_b(auth_headers, "some-url-capable-client", url_elicitation=True)

    assert "error" in msg, msg
    assert msg["error"]["code"] == _URL_ELICITATION_REQUIRED
    assert "/consent/server?nonce=" in msg["error"]["data"]["elicitations"][0]["url"]


def test_claude_code_without_declared_capability_gets_fallback_on_another_pod(auth_headers):
    """Claude Desktop identifies as `claude-code` but declares no URL elicitation and hangs on -32042, so
    pod B must answer with the plain-text fallback CallToolResult — the name alone grants nothing."""
    msg = _initialize_on_a_then_call_on_b(auth_headers, "claude-code", url_elicitation=False)

    assert "result" in msg, msg
    assert msg["result"]["isError"] is True
    assert "/consent/server?nonce=" in msg["result"]["content"][0]["text"]


def test_downstream_progress_notification_rides_post_sse_stream(auth_headers):
    """In stateless mode a downstream progress notification reaches the client on the POST's own SSE stream,
    ahead of the tool result (the JSON-response path would drop it)."""
    progress = {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"progressToken": "tok-1", "progress": 0.5, "total": 1.0},
    }

    def downstream(request: httpx.Request) -> httpx.Response:
        request_id = json.loads(request.content)["id"]
        result = {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "done"}]}}
        body = f"event: message\ndata: {json.dumps(progress)}\n\nevent: message\ndata: {json.dumps(result)}\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(downstream))
    with _pod_client(_container_stub(_FakeRedis(), has_consent=True, proxy_client=proxy_client)) as pod:
        msgs = _post(pod, _execute_tool(progress_token="tok-1"), auth_headers)

    assert [m.get("method") for m in msgs[:-1]] == ["notifications/progress"]
    assert msgs[0]["params"]["progressToken"] == "tok-1"
    assert msgs[-1]["result"]["content"][0]["text"] == "done"


def test_tools_list_served_without_session_affinity(auth_headers):
    """Stateless: a request carrying no Mcp-Session-Id is served by a pod that never saw an initialize for it
    (what happens when a load balancer routes to a different pod)."""
    with _pod_client(_container_stub(_FakeRedis(), has_consent=True)) as pod:
        msgs = _post(pod, _tools_list(), auth_headers)

    assert "result" in msgs[-1], "tools/list SSE body was truncated/cancelled"


def test_stale_session_id_is_served_not_404(auth_headers):
    """A client reconnecting with a session id from a since-reaped pod must be served, not rejected."""
    headers = {**auth_headers, "Mcp-Session-Id": "stale-session-from-another-pod"}
    with _pod_client(_container_stub(_FakeRedis(), has_consent=True)) as pod:
        msgs = _post(pod, _tools_list(), headers)

    assert "result" in msgs[-1], "stale-session SSE body was truncated/cancelled"
