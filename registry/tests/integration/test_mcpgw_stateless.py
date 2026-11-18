"""Integration coverage for the stateless MCP gateway.

Each "pod" is a full app built by the production ``create_app`` (real auth middleware, the mounted MCP app
wrapped in ``ClientInfoCaptureMiddleware``) with its own FastMCP session manager. Pods share only a fake
Redis. A request with no ``Mcp-Session-Id`` affinity is served by any pod, a stale session id is ignored
rather than 404'd, and the capability a client declares in ``initialize`` on one pod decides whether
another pod answers a ``tools/call`` with a ``-32042`` URL elicitation or the plain-text fallback.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI
from mcp import Client
from mcp.client.caching import InMemoryResponseCacheStore
from mcp.client.session import ClientRequestContext, ElicitationFnT
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.inbound import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
)
from mcp.types import (
    CallToolResult,
    ClientCapabilities,
    ElicitationCapability,
    ElicitRequestParams,
    ElicitResult,
    Implementation,
    TextContent,
    UrlElicitationCapability,
)
from starlette.testclient import TestClient

from registry.app_factory import create_app
from registry.constants import UrlElicitationInputKey
from registry.core.config import settings
from registry.core.mcp_downstream import DownstreamCallLimiter, DownstreamEraCache
from registry.mcpgw import create_gateway_mcp_app
from registry.mcpgw.core.client_info_store import ClientInfoStore
from registry.mcpgw.tools import server as server_tools
from registry_pkgs.core.exceptions import UrlElicitationRequiredException
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token
from tests.mcp_downstream_support import sdk_endpoint

pytestmark = [pytest.mark.integration]

USER_ID = "507f1f77bcf86cd799439011"
CLIENT_ID = "mcp-client-abc"  # DCR id -> cached under (user, client) without a jti
SERVER_ID = str(PydanticObjectId())
_MCP_PATH = "/proxy/mcpgw/mcp"
_MCP_ACCEPT = "application/json, text/event-stream"
_URL_ELICITATION_REQUIRED = -32042
_MODERN_VERSION = "2026-07-28"


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

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


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
    downstream_transport: httpx2.AsyncBaseTransport | None = None,
) -> SimpleNamespace:
    """A per-pod container: its own ClientInfoStore and mocks, sharing only the Redis stand-in."""
    server_service = MagicMock()
    server_service.get_server_by_id = AsyncMock(return_value=_server_doc())
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value={SERVER_ID})
    consent_store = MagicMock()
    consent_store.has_server_consent.return_value = has_consent
    return SimpleNamespace(
        mcpgw_downstream_transport=downstream_transport or MagicMock(),
        mcpgw_downstream_call_limiter=DownstreamCallLimiter(100),
        mcpgw_downstream_response_cache=InMemoryResponseCacheStore(),
        downstream_era_cache=DownstreamEraCache(redis, settings.redis_key_prefix),
        a2a_client_registry=MagicMock(),
        server_service=server_service,
        mcp_server_repo=MagicMock(),
        a2a_agent_repo=MagicMock(),
        search_service=MagicMock(),
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
    if resp.headers.get("content-type", "").startswith("application/json"):
        return [resp.json()]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2025-11-25", _MODERN_VERSION])
@pytest.mark.parametrize("token", ["tok-1", 0, None])
async def test_downstream_progress_notification_rides_post_sse_stream(
    auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, version: str, token: str | int | None
) -> None:
    """In stateless mode a downstream progress notification reaches the client on the POST's own SSE stream,
    ahead of the tool result (the JSON-response path would drop it)."""
    monkeypatch.setattr(settings, "mcpgw_modern_protocol_enabled", version == _MODERN_VERSION)

    async with sdk_endpoint(progress=True) as downstream:
        container = _container_stub(_FakeRedis(), has_consent=True, downstream_transport=downstream)
        app = _make_pod(container)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://localhost") as pod:
                if version == _MODERN_VERSION:
                    body, headers = _modern_execute_tool(url_elicitation=False), _modern_headers(auth_headers)
                else:
                    body, headers = _execute_tool(), auth_headers
                meta = body["params"].setdefault("_meta", {})
                meta["traceparent"] = "00-11111111111111111111111111111111-2222222222222222-01"
                if token is not None:
                    meta["progressToken"] = token
                response = await pod.post(_MCP_PATH, json=body, headers=headers)
                assert response.status_code == 200
                msgs = (
                    [response.json()]
                    if response.headers.get("content-type", "").startswith("application/json")
                    else _sse_messages(response.text)
                )

    assert [m.get("method") for m in msgs[:-1]] == (["notifications/progress"] if token is not None else [])
    if token is not None:
        assert msgs[0]["params"]["progressToken"] == token
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


def test_modern_protocol_header_rejected_when_gate_off(auth_headers):
    """With the kill switch off, a 2026-07-28 request to the gateway is rejected with -32022 before it
    reaches the MCP app, so clients fall back to 2025-11-25."""
    headers = {**auth_headers, "MCP-Protocol-Version": "2026-07-28"}
    with _pod_client(_container_stub(_FakeRedis(), has_consent=True)) as pod:
        resp = pod.post(_MCP_PATH, content=json.dumps(_tools_list()).encode(), headers=headers)

    assert resp.status_code == 400
    payload = resp.json()
    assert payload["id"] == 2
    assert payload["error"]["code"] == -32022
    assert payload["error"]["data"]["requested"] == "2026-07-28"


def test_handshake_protocol_header_passes_the_gate(auth_headers):
    """A handshake-era header is not gated; the request reaches the MCP app and is served."""
    headers = {**auth_headers, "MCP-Protocol-Version": "2025-11-25"}
    with _pod_client(_container_stub(_FakeRedis(), has_consent=True)) as pod:
        msgs = _post(pod, _tools_list(), headers)

    assert "result" in msgs[-1]


# --- 2026-07-28 modern era (kill switch on) -------------------------------------------------------
#
# A modern client sends no `initialize`: it carries the protocol version and its capabilities in the
# request `params._meta` envelope, matched by `MCP-Protocol-Version` / `MCP-Method` / `MCP-Name` headers,
# and answers an `InputRequiredResult` by retrying the same `tools/call` with `inputResponses` in `params`.


@pytest.fixture
def _modern_gate_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcpgw_modern_protocol_enabled", True)


def _modern_headers(auth_headers: dict[str, str]) -> dict[str, str]:
    return {
        **auth_headers,
        "MCP-Protocol-Version": _MODERN_VERSION,
        "MCP-Method": "tools/call",
        "MCP-Name": "execute_tool",
    }


def _modern_meta(*, url_elicitation: bool) -> dict:
    elicitation = ElicitationCapability(url=UrlElicitationCapability()) if url_elicitation else None
    return {
        PROTOCOL_VERSION_META_KEY: _MODERN_VERSION,
        CLIENT_CAPABILITIES_META_KEY: ClientCapabilities(elicitation=elicitation).model_dump(
            mode="json", exclude_none=True
        ),
        CLIENT_INFO_META_KEY: Implementation(name="modern-client", version="1.0").model_dump(mode="json"),
    }


def _modern_post(client: TestClient, body: dict, headers: dict[str, str]) -> dict:
    """POST a modern request and return its final JSON-RPC message. The single-exchange modern path may
    answer with a plain JSON response or an SSE stream, so accept either."""
    resp = client.post(_MCP_PATH, content=json.dumps(body).encode(), headers=headers)
    assert resp.status_code == 200, resp.text
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        return _sse_messages(resp.text)[-1]
    return resp.json()


def _modern_execute_tool(*, url_elicitation: bool, input_responses: dict | None = None, request_id: int = 2) -> dict:
    params: dict = {
        "name": "execute_tool",
        "arguments": {"tool_name": "tavily_search", "server_id": SERVER_ID, "arguments": {}},
        "_meta": _modern_meta(url_elicitation=url_elicitation),
    }
    if input_responses is not None:
        params["inputResponses"] = input_responses
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


@pytest.mark.parametrize("include_client_info", [True, False])
def test_modern_first_round_returns_input_required_golden_shape(
    auth_headers: dict[str, str], _modern_gate_on: None, include_client_info: bool
) -> None:
    """Golden wire assertions on the raw first response: a URL-capable 2026-07-28 client gets an
    `input_required` result in URL mode, with neither `elicitationId` nor `requestState` on the wire."""
    redis = _FakeRedis()
    body = _modern_execute_tool(url_elicitation=True)
    if not include_client_info:
        body["params"]["_meta"].pop(CLIENT_INFO_META_KEY)
    with _pod_client(_container_stub(redis, has_consent=False)) as pod:
        msg = _modern_post(pod, body, _modern_headers(auth_headers))

    result = msg["result"]
    assert result["resultType"] == "input_required"
    request = result["inputRequests"][UrlElicitationInputKey.CONSENT]
    assert request["params"]["mode"] == "url"
    assert "/consent/server?nonce=" in request["params"]["url"]
    assert "elicitationId" not in request["params"]
    assert "requestState" not in result
    assert redis.store == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "grant_consent"),
    [
        ("accept", False),
        ("accept", True),
        ("decline", False),
        ("cancel", False),
        ("decline", True),
        ("cancel", True),
        (None, False),
    ],
)
async def test_modern_sdk_drives_authorization_to_terminal_result(
    auth_headers: dict[str, str],
    _modern_gate_on: None,
    action: Literal["accept", "decline", "cancel"] | None,
    grant_consent: bool,
) -> None:
    """The real SDK dispatches one URL prompt and retries once; incapable clients do neither."""
    outgoing: list[dict[str, Any]] = []
    prompts: list[ElicitRequestParams] = []

    async def record_request(request: httpx2.Request) -> None:
        if request.method == "POST":
            outgoing.append(json.loads(await request.aread()))

    async with sdk_endpoint() as downstream:
        redis = _FakeRedis()
        container = _container_stub(redis, has_consent=False, downstream_transport=downstream)

        async def answer(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
            assert action is not None
            assert params.mode == "url"
            assert "/consent/server?nonce=" in params.url
            prompts.append(params)
            if grant_consent:
                container.consent_store.has_server_consent.return_value = True
            return ElicitResult(action=action)

        app = _make_pod(container)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost",
                headers=auth_headers,
                event_hooks={"request": [record_request]},
            ) as http_client:
                async with Client(
                    streamable_http_client(f"http://localhost{_MCP_PATH}", http_client=http_client),
                    mode=_MODERN_VERSION,
                    elicitation_callback=answer if action is not None else None,
                ) as client:
                    assert client.protocol_version == _MODERN_VERSION
                    await client.list_tools()
                    result = await client.call_tool(
                        "execute_tool", {"tool_name": "tavily_search", "server_id": SERVER_ID, "arguments": {}}
                    )

    calls = [body for body in outgoing if body["method"] == "tools/call"]
    assert all(body["method"] != "initialize" for body in outgoing)
    assert len(calls) == (2 if action is not None else 1)
    assert container.server_service.get_server_by_id.await_count == len(calls)
    assert len(prompts) == (1 if action is not None else 0)
    assert "inputResponses" not in calls[0]["params"]
    assert all("requestState" not in body["params"] for body in calls)
    if action is not None:
        assert calls[1]["params"]["inputResponses"][UrlElicitationInputKey.CONSENT]["action"] == action
        assert calls[0]["params"]["arguments"] == calls[1]["params"]["arguments"]
    executes = grant_consent and action == "accept"
    assert downstream.count("tools/call") == int(executes)
    assert isinstance(result, CallToolResult)
    assert result.is_error is not executes
    assert isinstance(result.content[0], TextContent)
    if executes:
        assert result.content[0].text == "done"
    elif action in {"decline", "cancel"}:
        assert "declined or cancelled" in result.content[0].text
    else:
        assert "/consent/server?nonce=" in result.content[0].text
    assert redis.store == ({f"{settings.redis_key_prefix}:mcp_era:{SERVER_ID}": _MODERN_VERSION} if executes else {})


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_oauth", [True, False])
async def test_modern_sdk_prompts_once_per_check_through_consent_then_oauth(
    auth_headers: dict[str, str],
    _modern_gate_on: None,
    monkeypatch: pytest.MonkeyPatch,
    complete_oauth: bool,
) -> None:
    """First use of an OAuth-backed server: the consent prompt is answered, the retry then fails the OAuth
    check and gets its own URL prompt (not text). If the OAuth check still fails after its own prompt, the
    third round returns text, so the chain stops at three calls."""
    outgoing: list[dict[str, Any]] = []
    prompts: list[str] = []
    tokens = {"present": False}

    async def record_request(request: httpx2.Request) -> None:
        if request.method == "POST":
            outgoing.append(json.loads(await request.aread()))

    async def headers_or_reauth(*, additional_headers: dict[str, str], **_kwargs) -> dict[str, str]:
        if not tokens["present"]:
            raise UrlElicitationRequiredException(
                "authorize", auth_url="https://registry.example/oauth/authorize?state=s", server_name="github"
            )
        return dict(additional_headers)

    monkeypatch.setattr(server_tools, "build_authenticated_headers", headers_or_reauth)

    async with sdk_endpoint() as downstream:
        container = _container_stub(_FakeRedis(), has_consent=False, downstream_transport=downstream)

        async def answer(_ctx: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
            prompts.append(params.url)
            if "/consent/server?nonce=" in params.url:
                container.consent_store.has_server_consent.return_value = True
            elif complete_oauth:
                tokens["present"] = True
            return ElicitResult(action="accept")

        app = _make_pod(container)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost",
                headers=auth_headers,
                event_hooks={"request": [record_request]},
            ) as http_client:
                async with Client(
                    streamable_http_client(f"http://localhost{_MCP_PATH}", http_client=http_client),
                    mode=_MODERN_VERSION,
                    elicitation_callback=answer,
                ) as client:
                    result = await client.call_tool(
                        "execute_tool", {"tool_name": "tavily_search", "server_id": SERVER_ID, "arguments": {}}
                    )

    calls = [body for body in outgoing if body["method"] == "tools/call"]
    assert len(prompts) == 2
    assert "/consent/server?nonce=" in prompts[0]
    assert prompts[1] == "https://registry.example/oauth/authorize?state=s"
    assert [set(body["params"].get("inputResponses", {})) for body in calls[:2]] == [
        set(),
        {UrlElicitationInputKey.CONSENT},
    ]
    assert len(calls) == 3
    assert downstream.count("tools/call") == int(complete_oauth)
    assert isinstance(result.content[0], TextContent)
    if complete_oauth:
        assert result.is_error is not True
        assert result.content[0].text == "done"
    else:
        assert set(calls[2]["params"]["inputResponses"]) == {UrlElicitationInputKey.AUTHORIZATION}
        assert result.is_error is True
        assert "https://registry.example/oauth/authorize?state=s" in result.content[0].text


def test_modern_no_url_capability_gets_fallback(auth_headers, _modern_gate_on):
    """A 2026-07-28 client that declares no `elicitation.url` gets the plain-text fallback, not input_required."""
    with _pod_client(_container_stub(_FakeRedis(), has_consent=False)) as pod:
        msg = _modern_post(pod, _modern_execute_tool(url_elicitation=False), _modern_headers(auth_headers))

    result = msg["result"]
    assert result.get("resultType") != "input_required"
    assert result["isError"] is True
    assert "/consent/server?nonce=" in result["content"][0]["text"]


def test_modern_retry_accept_without_consent_returns_url_text(auth_headers, _modern_gate_on):
    """The retry re-runs the consent check (no request_state is trusted). Consent is still missing, so the
    second round returns the URL text rather than prompting again — the server never loops."""
    container = _container_stub(_FakeRedis(), has_consent=False)
    with _pod_client(container) as pod:
        first = _modern_post(pod, _modern_execute_tool(url_elicitation=True), _modern_headers(auth_headers))
        assert first["result"]["resultType"] == "input_required"

        retry = _modern_execute_tool(
            url_elicitation=True,
            input_responses={UrlElicitationInputKey.CONSENT: {"action": "accept"}},
            request_id=3,
        )
        second = _modern_post(pod, retry, _modern_headers(auth_headers))

    result = second["result"]
    assert result.get("resultType") != "input_required"
    assert result["isError"] is True
    assert "/consent/server?nonce=" in result["content"][0]["text"]


async def test_modern_retry_accept_with_consent_reaches_downstream(auth_headers, _modern_gate_on):
    async with sdk_endpoint() as downstream:
        container = _container_stub(_FakeRedis(), has_consent=False, downstream_transport=downstream)
        app = _make_pod(container)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://localhost") as pod:
                first = await pod.post(
                    _MCP_PATH, json=_modern_execute_tool(url_elicitation=True), headers=_modern_headers(auth_headers)
                )
                assert first.json()["result"]["resultType"] == "input_required"
                container.consent_store.has_server_consent.return_value = True
                retry = _modern_execute_tool(
                    url_elicitation=True,
                    input_responses={UrlElicitationInputKey.CONSENT: {"action": "accept"}},
                    request_id=3,
                )
                second = await pod.post(_MCP_PATH, json=retry, headers=_modern_headers(auth_headers))
                assert second.json()["result"]["content"][0]["text"] == "done"
        assert downstream.count("tools/call") == 1


def test_modern_request_rejected_when_gate_off(auth_headers):
    """With the kill switch off a 2026-07-28 tools/call is rejected with -32022 before the MCP app, so the
    client renegotiates 2025-11-25 (companion to the handshake-era -32042 path above)."""
    body = _modern_execute_tool(url_elicitation=True)
    with _pod_client(_container_stub(_FakeRedis(), has_consent=False)) as pod:
        resp = pod.post(_MCP_PATH, content=json.dumps(body).encode(), headers=_modern_headers(auth_headers))

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32022


@asynccontextmanager
async def _sdk_client(
    container: SimpleNamespace,
    auth: dict[str, str],
    *,
    callback: ElicitationFnT | None = None,
    mode: str = "2026-07-28",
    outgoing: list[dict[str, Any]] | None = None,
) -> AsyncIterator[Client]:
    async def record(request: httpx2.Request) -> None:
        if outgoing is not None and request.method == "POST":
            outgoing.append(json.loads(await request.aread()))

    app = _make_pod(container)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost",
            headers=auth,
            event_hooks={"request": [record]},
        ) as http:
            async with Client(
                streamable_http_client(f"http://localhost{_MCP_PATH}", http_client=http),
                mode=mode,
                elicitation_callback=callback,
            ) as client:
                yield client


@pytest.mark.parametrize(
    "action,grant",
    [
        ("accept", True),
        ("accept", False),
        ("decline", False),
        ("cancel", False),
        ("decline", True),
        ("cancel", True),
        (None, False),
    ],
)
async def test_agent_sdk_authorization_rounds(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    _modern_gate_on: None,
    action: Literal["accept", "decline", "cancel"] | None,
    grant: bool,
) -> None:
    from registry.mcpgw.tools import agent as agent_tools
    from tests.unit.mcpgw.test_agent import _make_agent, _result_with_message

    container = _container_stub(_FakeRedis(), has_consent=False)
    container.consent_store.has_agent_consent.return_value = False
    container.a2a_client_registry.get_client = AsyncMock(return_value=MagicMock())
    container.acl_service.get_accessible_resource_ids.return_value = {SERVER_ID}
    model = MagicMock()
    model.find_one = AsyncMock(return_value=_make_agent(SERVER_ID))
    monkeypatch.setattr(agent_tools, "A2AAgent", model)
    invoke = AsyncMock(return_value=_result_with_message("agent-done"))
    monkeypatch.setattr(agent_tools, "call_a2a", invoke)
    prompts: list[ElicitRequestParams] = []
    outgoing: list[dict[str, Any]] = []

    async def answer(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
        assert action is not None
        assert params.mode == "url" and "/consent/agent?nonce=" in params.url
        prompts.append(params)
        if grant:
            container.consent_store.has_agent_consent.return_value = True
        return ElicitResult(action=action)

    async with _sdk_client(container, auth_headers, callback=answer if action else None, outgoing=outgoing) as client:
        result = await client.call_tool(
            "execute_agent", {"agent_id": SERVER_ID, "message": {"parts": [{"kind": "text", "text": "do task"}]}}
        )
    assert isinstance(result, CallToolResult)
    executes = action == "accept" and grant
    assert result.is_error is not executes
    assert invoke.await_count == int(executes)
    assert len(prompts) == int(action is not None)
    calls = [message for message in outgoing if message["method"] == "tools/call"]
    assert len(calls) == (2 if action else 1)
    assert container.pending_consent_store.save.call_count == (2 if action == "accept" and not grant else 1)
    assert isinstance(result.content[0], TextContent)
    if action in {"decline", "cancel"}:
        assert "declined or cancelled" in result.content[0].text
    if executes:
        assert "agent-done" in result.content[0].text
    assert all("requestState" not in call["params"] for call in calls)


@pytest.mark.parametrize("via_401", [False, True])
@pytest.mark.parametrize("action,grant", [("accept", True), ("accept", False), ("decline", True), ("cancel", True)])
async def test_oauth_sdk_authorization_and_recovery(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    _modern_gate_on: None,
    via_401: bool,
    action: Literal["accept", "decline", "cancel"],
    grant: bool,
) -> None:
    from registry.utils.mcp_headers import build_authenticated_headers

    # Exercise production header construction, OAuth exception mapping, and recovery.
    # Only the external OAuth service and HTTP target are boundary stand-ins.
    monkeypatch.setattr(server_tools, "build_authenticated_headers", build_authenticated_headers)
    authorized = via_401
    prompts: list[ElicitRequestParams] = []
    get_tokens = AsyncMock()

    async def tokens(**kwargs: Any) -> tuple[str | None, str | None, str | None]:
        return ("test-token", None, None) if authorized else (None, "https://login.example/authorize", None)

    get_tokens.side_effect = tokens

    async def recover(*args: Any, **kwargs: Any) -> None:
        nonlocal authorized
        authorized = False

    async with sdk_endpoint(result=CallToolResult(content=[TextContent(type="text", text="oauth-done")])) as downstream:
        if via_401:
            downstream.failure_method = "tools/list"
            downstream.fail_once = True
            downstream.challenge = 'Bearer resource_metadata="https://downstream.example/metadata"'
        container = _container_stub(_FakeRedis(), has_consent=True, downstream_transport=downstream)
        target = _server_doc()
        target.mcp_auth_mode = server_tools.McpAuthMode.OAUTH
        target.config["requiresOAuth"] = True
        container.server_service.get_server_by_id.return_value = target
        container.oauth_service.get_valid_access_token = get_tokens
        container.oauth_service.recover_from_unauthorized = AsyncMock(side_effect=recover)

        async def answer(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
            nonlocal authorized
            assert params.mode == "url" and params.url == "https://login.example/authorize"
            prompts.append(params)
            if grant:
                authorized = True
            return ElicitResult(action=action)

        async with _sdk_client(container, auth_headers, callback=answer) as client:
            result = await client.call_tool(
                "execute_tool", {"server_id": SERVER_ID, "tool_name": "search", "arguments": {}}
            )
        executes = action == "accept" and grant
        assert isinstance(result, CallToolResult)
        assert result.is_error is not executes
        assert len(prompts) == 1
        assert downstream.count("tools/call") == int(executes)
        assert all(h["authorization"] == "Bearer test-token" for h in downstream.headers)
        assert get_tokens.await_count == (int(via_401) + 1 + int(action == "accept"))
        assert container.oauth_service.recover_from_unauthorized.await_count == int(via_401)
        if via_401:
            assert container.oauth_service.recover_from_unauthorized.await_args.kwargs["is_retry"] is False
            assert (
                "downstream.example/metadata"
                in container.oauth_service.recover_from_unauthorized.await_args.kwargs["www_authenticate"]
            )
        assert isinstance(result.content[0], TextContent)
        if executes:
            assert result.content[0].text == "oauth-done"
        elif action == "accept":
            assert "https://login.example/authorize" in result.content[0].text
        else:
            assert "declined or cancelled" in result.content[0].text
        assert all(
            not set(UrlElicitationInputKey) & set(send["params"].get("inputResponses", {}))
            for send in downstream.messages
            if send["method"] == "tools/call"
        )


async def test_sdk_auto_with_gate_off_negotiates_legacy_and_elicits(
    monkeypatch: pytest.MonkeyPatch, auth_headers: dict[str, str]
) -> None:
    from mcp.shared.exceptions import MCPError

    monkeypatch.setattr(settings, "mcpgw_modern_protocol_enabled", False)
    container = _container_stub(_FakeRedis(), has_consent=False)
    outgoing: list[dict[str, Any]] = []

    async def answer(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
        return ElicitResult(action="accept")

    async with _sdk_client(container, auth_headers, callback=answer, mode="auto", outgoing=outgoing) as client:
        assert client.protocol_version == "2025-11-25"
        with pytest.raises(MCPError) as error:
            await client.call_tool("execute_tool", {"server_id": SERVER_ID, "tool_name": "search", "arguments": {}})
        assert error.value.code == -32042
    assert [call["method"] for call in outgoing][:2] == ["server/discover", "initialize"]
    container.consent_store.has_server_consent.assert_called_once()
