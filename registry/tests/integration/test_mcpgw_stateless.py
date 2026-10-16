"""Integration coverage for the stateless MCP gateway.

Two independent FastMCP instances (separate process-local session state, as two pods would have) share
one Redis-backed client-info cache. A request with no ``Mcp-Session-Id`` affinity is served by either
instance, a stale session id is ignored rather than 404'd, and the client info captured on one instance
is visible to the other so URL-mode elicitation survives across pods.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mcp.types import ClientCapabilities, ElicitationCapability, Implementation, UrlElicitationCapability
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from registry.core.config import settings
from registry.mcpgw import create_gateway_mcp_app
from registry.mcpgw.core.client_info_store import ClientInfoCaptureMiddleware, ClientInfoStore
from registry.mcpgw.tools.utils import _support_url_elicitation, get_client_params

pytestmark = [pytest.mark.integration]

USER_ID = "507f1f77bcf86cd799439011"
CLIENT_ID = "mcp-client-abc"  # DCR id -> cached under (user, client) without a jti
_MCP_ACCEPT = "application/json, text/event-stream"


@pytest.fixture(autouse=True)
def _disable_dns_rebinding_protection(monkeypatch: pytest.MonkeyPatch):
    """TestClient sends Host: testserver, which the gateway's DNS-rebinding guard would reject (421).
    That guard is orthogonal to statelessness, so turn it off for these transport-level tests."""
    monkeypatch.setattr(settings, "mcpgw_enable_dns_rebinding_protection", False)


class _FakeRedis:
    """Minimal shared Redis stand-in for cross-instance client-info."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    def get(self, key: str) -> str | None:
        return self.store.get(key)


class _UserStateMiddleware:
    """Stand in for UnifiedAuthMiddleware: pin an authenticated user onto the request scope."""

    def __init__(self, app, user: dict) -> None:
        self._app = app
        self._user = user

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["user"] = self._user
        await self._app(scope, receive, send)


def _container_stub(store: ClientInfoStore) -> SimpleNamespace:
    """A container exposing every attribute mcp_lifespan reads; only the client-info store is real."""
    return SimpleNamespace(
        mcpgw_proxy_client=MagicMock(),
        a2a_client_registry=MagicMock(),
        server_service=MagicMock(),
        mcp_server_repo=MagicMock(),
        a2a_agent_repo=MagicMock(),
        search_service=MagicMock(),
        mcp_client_service=MagicMock(),
        oauth_service=MagicMock(),
        client_info_store=store,
        redis_client=MagicMock(),
        acl_service=MagicMock(),
        consent_store=MagicMock(),
        pending_consent_store=MagicMock(),
    )


def _make_instance(store: ClientInfoStore):
    """Build one standalone gateway instance (its own session manager) sharing the given store."""
    mcp = create_gateway_mcp_app(container_provider=lambda: _container_stub(store))
    gateway = mcp.streamable_http_app()
    # Added last = outermost: inject the user before the capture middleware reads it.
    gateway.add_middleware(ClientInfoCaptureMiddleware, store_provider=lambda: store)
    gateway.add_middleware(_UserStateMiddleware, user={"user_id": USER_ID, "client_id": CLIENT_ID})
    return mcp, gateway


def _initialize_body(client_name: str, *, url_elicitation: bool) -> bytes:
    elicitation = ElicitationCapability(url=UrlElicitationCapability()) if url_elicitation else None
    caps = ClientCapabilities(elicitation=elicitation)
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": caps.model_dump(mode="json", exclude_none=True),
                "clientInfo": Implementation(name=client_name, version="1.0").model_dump(mode="json"),
            },
        }
    ).encode()


def _tools_list_body() -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode()


def _sse_result(resp_text: str) -> dict | None:
    """Parse the first JSON-RPC message from an SSE response body, or None if the body was truncated.

    A complete `data:` event proves the streaming response ran to completion — the guard against the
    capture middleware's receive replay cancelling the SSE stream before its result is written.
    """
    for line in resp_text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                return None
    return None


def test_initialize_on_one_instance_visible_to_another_via_redis():
    """A claude-code client (URL-elicitation capable) initializing on instance A must be visible to
    instance B through shared Redis, so B would send a -32042 URL elicitation, not plain text."""
    store = ClientInfoStore(_FakeRedis(), settings.redis_key_prefix, settings.jwt_token_config)
    _, gateway_a = _make_instance(store)

    with TestClient(gateway_a, base_url="http://localhost") as client_a:
        resp = client_a.post(
            "/mcp",
            content=_initialize_body("claude-code", url_elicitation=True),
            headers={"Content-Type": "application/json", "Accept": _MCP_ACCEPT},
        )
    assert resp.status_code == 200
    init_msg = _sse_result(resp.text)
    assert init_msg is not None and "result" in init_msg, "initialize SSE body was truncated/cancelled"

    # Instance B reads the same Redis: build a B-side request context and resolve the client params.
    ctx_b = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(state=SimpleNamespace(user={"user_id": USER_ID, "client_id": CLIENT_ID})),
            lifespan_context=SimpleNamespace(client_info_store=store),
        )
    )
    params_b = get_client_params(ctx_b)
    assert params_b is not None
    assert params_b.clientInfo.name == "claude-code"
    assert _support_url_elicitation(params_b) is True


def test_tools_list_served_without_session_affinity():
    """Stateless: a request carrying no Mcp-Session-Id is served by an instance that never saw an
    initialize for it (what happens when a load balancer routes to a different pod)."""
    store = ClientInfoStore(_FakeRedis(), settings.redis_key_prefix, settings.jwt_token_config)
    _, gateway_b = _make_instance(store)

    with TestClient(gateway_b, base_url="http://localhost") as client_b:
        resp = client_b.post(
            "/mcp",
            content=_tools_list_body(),
            headers={"Content-Type": "application/json", "Accept": _MCP_ACCEPT},
        )

    assert resp.status_code == 200
    msg = _sse_result(resp.text)
    assert msg is not None and "result" in msg, "tools/list SSE body was truncated/cancelled"


def test_stale_session_id_is_served_not_404():
    """A client reconnecting with a session id from a since-reaped pod must be served, not rejected."""
    store = ClientInfoStore(_FakeRedis(), settings.redis_key_prefix, settings.jwt_token_config)
    _, gateway = _make_instance(store)

    with TestClient(gateway, base_url="http://localhost") as client:
        resp = client.post(
            "/mcp",
            content=_tools_list_body(),
            headers={
                "Content-Type": "application/json",
                "Accept": _MCP_ACCEPT,
                "Mcp-Session-Id": "stale-session-from-another-pod",
            },
        )

    assert resp.status_code == 200
    msg = _sse_result(resp.text)
    assert msg is not None and "result" in msg, "stale-session SSE body was truncated/cancelled"
