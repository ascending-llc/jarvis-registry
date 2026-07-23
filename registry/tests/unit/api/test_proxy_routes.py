"""Tests for ObjectId validation in the dynamic proxy GET/POST handlers.

The spec required updating proxy-route assertions for the new {user_id}/{server_path} URL
format. These tests verify that both handlers reject non-ObjectId user_id values with a
400 and a ``{"detail": ...}`` body before any further processing occurs.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
from starlette.requests import Request

from registry.api.proxy_routes import dynamic_mcp_get_proxy, dynamic_mcp_post_proxy, http_json_proxy, jsonrpc_proxy

VALID_OBJECT_ID = "507f1f77bcf86cd799439011"
INVALID_USER_IDS = ["mcpgw", "not-an-objectid", "123", ""]

_AUTH_CONTEXT = {"auth_method": "bearer", "user_id": VALID_OBJECT_ID, "username": "test", "client_id": "claude"}


def _make_server(*, enabled: bool = True):
    return SimpleNamespace(
        id=PydanticObjectId(),
        path="/github",
        serverName="github",
        config={"enabled": enabled, "type": "streamable-http", "url": "https://example.com/mcp"},
    )


def _server_service(server):
    service = AsyncMock()
    service.extract_server_path.return_value = "/github"
    service.get_server_by_path.return_value = server
    return service


def _acl_service(*, denied: bool = False):
    service = AsyncMock()
    if denied:
        service.check_user_permission.side_effect = HTTPException(status_code=403)
    else:
        service.check_user_permission.return_value = None
    return service


def _consent_store(*, has_server_consent: bool = True):
    store = Mock()
    store.has_server_consent.return_value = has_server_consent
    return store


class _PendingConsentStore:
    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}

    def save(self, nonce: str, data: dict) -> None:
        self.pending[nonce] = data


def _post_request(user_id: str, server_path: str = "github") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": f"/proxy/server/{user_id}/{server_path}",
        "query_string": b"",
        "headers": [],
        "path_params": {"user_id": user_id, "server_path": server_path},
    }
    return Request(scope)


def _get_request(user_id: str, server_path: str = "github") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": f"/proxy/server/{user_id}/{server_path}",
        "query_string": b"",
        "headers": [(b"accept", b"text/event-stream")],
        "path_params": {"user_id": user_id, "server_path": server_path},
    }
    return Request(scope)


def _a2a_request(method: str = "POST") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "scheme": "http",
        "server": ("testserver", 80),
        "path": "/gateway/proxy/a2a/test-agent",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer caller-token")],
        "path_params": {"agent_path": "test-agent"},
    }
    return Request(scope)


def _a2a_agent():
    return SimpleNamespace(
        id=PydanticObjectId(),
        path="test-agent",
        config=SimpleNamespace(enabled=True, runtimeAccess=None, url=None),
        card=SimpleNamespace(url="https://agent.example.com/a2a"),
        federationMetadata={"providerType": "azure_ai_foundry"},
    )


@pytest.mark.parametrize("user_id", INVALID_USER_IDS)
async def test_post_proxy_rejects_invalid_user_id(user_id):
    resp = await dynamic_mcp_post_proxy(
        request=_post_request(user_id),
        user_id=user_id,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=Mock(),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
    )
    assert resp.status_code == 400

    body = json.loads(resp.body)
    assert "detail" in body
    assert "error" not in body


@pytest.mark.parametrize("user_id", INVALID_USER_IDS)
async def test_get_proxy_rejects_invalid_user_id(user_id):
    resp = await dynamic_mcp_get_proxy(
        request=_get_request(user_id),
        user_id=user_id,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=Mock(),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
    )
    assert resp.status_code == 400

    body = json.loads(resp.body)
    assert "detail" in body
    assert "error" not in body


async def test_post_proxy_does_not_reject_valid_object_id(monkeypatch):
    """A valid ObjectId must not be rejected at the user_id guard — processing continues."""
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )
    monkeypatch.setattr("registry.api.proxy_routes._is_notification", Mock(return_value=False))
    monkeypatch.setattr("registry.api.proxy_routes._extract_request_id", Mock(return_value=1))

    mock_server_service = AsyncMock()
    mock_server_service.extract_server_path.return_value = None

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=mock_server_service,
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
    )
    # ObjectId guard would produce {"detail": "...invalid user ID..."}.
    # Any other response (e.g. 404 for unknown server) means the guard passed.
    if resp.status_code == 400:
        body = json.loads(resp.body)
        assert "invalid user ID" not in body.get("detail", "")


async def test_post_proxy_acl_denied_returns_jsonrpc_error(monkeypatch):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server()),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(denied=True),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["result"]["isError"] is True
    assert "Access denied" in body["result"]["content"][0]["text"]


async def test_post_proxy_acl_allowed_continues(monkeypatch):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(enabled=False)),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=_consent_store(),
    )

    body = json.loads(resp.body)
    assert body["result"]["isError"] is True
    assert "Access denied" not in body["result"]["content"][0]["text"]


async def test_post_proxy_without_server_consent_returns_url_elicitation(monkeypatch):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )
    pending_store = _PendingConsentStore()
    consent_store = _consent_store(has_server_consent=False)

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server()),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=consent_store,
        pending_store=pending_store,
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["error"]["code"] == -32042
    assert body["error"]["data"]["elicitations"][0]["mode"] == "url"
    assert "/consent/server?nonce=" in body["error"]["data"]["elicitations"][0]["url"]
    UUID(body["error"]["data"]["elicitations"][0]["elicitationId"])
    assert len(pending_store.pending) == 1
    pending = next(iter(pending_store.pending.values()))
    assert pending == {"user_id": VALID_OBJECT_ID, "client_id": "claude", "server_path": "/github"}
    consent_store.has_server_consent.assert_called_once_with(VALID_OBJECT_ID, "claude", "/github")


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
async def test_post_proxy_without_server_consent_allows_handshake_methods(monkeypatch, method):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": method, "id": 1}),
    )
    consent_store = _consent_store(has_server_consent=False)

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(enabled=False)),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=consent_store,
        pending_store=_PendingConsentStore(),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert "error" not in body
    assert "disabled" in body["result"]["content"][0]["text"].lower()
    consent_store.has_server_consent.assert_not_called()


async def test_get_proxy_acl_denied_returns_403():
    resp = await dynamic_mcp_get_proxy(
        request=_get_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server()),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(denied=True),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 403
    assert "Access denied" in body["detail"]


async def test_get_proxy_acl_allowed_continues():
    resp = await dynamic_mcp_get_proxy(
        request=_get_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(enabled=False)),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 404
    assert "disabled" in body["detail"].lower()


async def test_jsonrpc_proxy_gets_client_from_a2a_client_registry(monkeypatch):
    agent = _a2a_agent()
    proxy_client = Mock()
    registry = SimpleNamespace(get_client=AsyncMock(return_value=proxy_client))
    a2a_agent_service = SimpleNamespace(get_agent_by_path=AsyncMock(return_value=agent))
    acl_service = SimpleNamespace(check_user_permission=AsyncMock(return_value=None))
    forward = AsyncMock(return_value=Mock(status_code=200))
    request = _a2a_request()
    monkeypatch.setattr("registry.api.proxy_routes._forward_a2a", forward)

    response = await jsonrpc_proxy(
        request=request,
        agent_path="test-agent",
        user_context=_AUTH_CONTEXT,
        a2a_agent_service=a2a_agent_service,
        acl_service=acl_service,
        a2a_client_registry=registry,
    )

    assert response.status_code == 200
    registry.get_client.assert_awaited_once_with(agent)
    forward.assert_awaited_once_with(
        request, "https://agent.example.com/a2a", proxy_client, "test-agent", is_jsonrpc=True
    )


async def test_http_json_proxy_gets_client_from_a2a_client_registry(monkeypatch):
    agent = _a2a_agent()
    proxy_client = Mock()
    registry = SimpleNamespace(get_client=AsyncMock(return_value=proxy_client))
    a2a_agent_service = SimpleNamespace(get_agent_by_path=AsyncMock(return_value=agent))
    acl_service = SimpleNamespace(check_user_permission=AsyncMock(return_value=None))
    forward = AsyncMock(return_value=Mock(status_code=200))
    request = _a2a_request(method="GET")
    monkeypatch.setattr("registry.api.proxy_routes._forward_a2a", forward)

    response = await http_json_proxy(
        request=request,
        agent_path="test-agent",
        http_json_path="tasks/1",
        user_context=_AUTH_CONTEXT,
        a2a_agent_service=a2a_agent_service,
        acl_service=acl_service,
        a2a_client_registry=registry,
    )

    assert response.status_code == 200
    registry.get_client.assert_awaited_once_with(agent)
    forward.assert_awaited_once_with(request, "https://agent.example.com/a2a/tasks/1", proxy_client, "test-agent")


def test_httpx_decoders_supported_decoders_is_accessible():
    """
    Canary for the private-API coupling in `_HTTPX_DECODABLE_CONTENT_ENCODINGS`
    (registry/src/registry/api/proxy_routes.py).

    That constant is derived from `httpx._decoders.SUPPORTED_DECODERS`, an underscore-prefixed
    module that is not part of httpx's public API and could be renamed, restructured, or removed
    in a future httpx version without a deprecation warning. If that ever happens, we want it
    caught here as a fast, obvious CI failure -- not later as a mystifying prod bug where the
    proxy silently mis-forwards still-compressed bytes because the derived set quietly went empty
    or the import broke in some less direct way.
    """
    import httpx

    assert frozenset(httpx._decoders.SUPPORTED_DECODERS.keys())
