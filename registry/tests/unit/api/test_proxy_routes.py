"""Tests for ObjectId validation in the dynamic proxy GET/POST handlers.

The spec required updating proxy-route assertions for the new {user_id}/{server_path} URL
format. These tests verify that both handlers reject non-ObjectId user_id values with a
400 and a ``{"detail": ...}`` body before any further processing occurs.
"""

import json
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock, call
from uuid import UUID

import httpx2
import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException, Response
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from starlette.requests import Request

from registry.api import proxy_routes
from registry.api.proxy_routes import (
    _serve_managed_agent_card,
    dynamic_mcp_get_proxy,
    dynamic_mcp_post_proxy,
    http_json_proxy,
    jsonrpc_proxy,
    managed_agent_card,
    managed_agent_card_well_known_alias,
    router,
)
from registry.constants import UrlElicitationInputKey
from registry.core.config import settings
from registry.core.proxy_call import ProxyCall
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.core.exceptions import DownstreamAuthRejectedException, UrlElicitationRequiredException
from registry_pkgs.models.a2a_agent import NoSupportedTransportError
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode, McpAuthMode
from registry_pkgs.testing.federation_metadata import make_azure_foundry_metadata

VALID_OBJECT_ID = "507f1f77bcf86cd799439011"
INVALID_USER_IDS = ["mcpgw", "not-an-objectid", "123", ""]

_AUTH_CONTEXT = {
    "auth_method": "bearer",
    "user_id": VALID_OBJECT_ID,
    "username": "test",
    "client_id": INTERACTIVE_CLIENT_ID,
}


def _make_server(
    *,
    enabled: bool = True,
    auth_mode: McpAuthMode = McpAuthMode.NONE,
    transport: str = "streamable-http",
    disabled_tools: list[str] | None = None,
):
    return SimpleNamespace(
        id=PydanticObjectId(),
        mcp_auth_mode=auth_mode,
        path="/github",
        serverName="github",
        config={"enabled": enabled, "type": transport, "url": "https://example.com/mcp"},
        registryDisabledTools=disabled_tools or [],
        is_tool_disabled=lambda tool_name: tool_name in (disabled_tools or []),
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
    return Request(scope, receive=_proxy_receive())


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
        federationMetadata=make_azure_foundry_metadata(),
    )


def _managed_card_service(agent: object | None, card: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        get_agent_by_path=AsyncMock(return_value=agent),
        build_managed_agent_card=Mock(return_value=card or {"name": "Managed Agent"}),
    )


async def test_managed_agent_card_routes_return_byte_identical_content() -> None:
    agent = _a2a_agent()
    service = _managed_card_service(agent, {"name": "Managed Agent", "preferredTransport": "JSONRPC"})

    primary = await managed_agent_card("test-agent", service)
    alias = await managed_agent_card_well_known_alias("test-agent", service)

    assert primary.status_code == 200
    assert primary.body == alias.body
    assert service.get_agent_by_path.await_count == 2
    assert service.build_managed_agent_card.call_args_list == [call(agent), call(agent)]


@pytest.mark.parametrize(
    "agent",
    [
        None,
        SimpleNamespace(config=None),
        SimpleNamespace(config=SimpleNamespace(enabled=False)),
    ],
    ids=["not-found", "missing-config", "disabled"],
)
async def test_managed_agent_card_returns_indistinguishable_not_found(agent: object | None) -> None:
    service = _managed_card_service(agent)

    response = await _serve_managed_agent_card("test-agent", service)

    assert response.status_code == 404
    assert response.body == b'{"detail":"A2A agent not found"}'
    service.build_managed_agent_card.assert_not_called()


async def test_managed_agent_card_is_available_for_enabled_iam_agent() -> None:
    agent = SimpleNamespace(
        config=SimpleNamespace(
            enabled=True,
            runtimeAccess=SimpleNamespace(mode=AgentCoreRuntimeAccessMode.IAM),
        )
    )
    service = _managed_card_service(agent)

    response = await _serve_managed_agent_card("iam-agent", service)

    assert response.status_code == 200
    service.build_managed_agent_card.assert_called_once_with(agent)


async def test_managed_agent_card_maps_unsupported_legacy_transport_to_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _a2a_agent()
    service = _managed_card_service(agent)
    service.build_managed_agent_card.side_effect = NoSupportedTransportError("legacy gRPC-only card")

    with caplog.at_level(logging.ERROR, logger="registry.api.proxy_routes"):
        response = await _serve_managed_agent_card("legacy-agent", service)

    assert response.status_code == 500
    assert response.body == b'{"detail":"Internal server error"}'
    assert "legacy-agent" in caplog.text
    assert "legacy gRPC-only card" not in response.body.decode()


def test_managed_agent_card_routes_are_registered_before_http_json_catch_all() -> None:
    paths = [route.path for route in router.routes]
    catch_all_index = paths.index("/a2a/{agent_path}/{http_json_path:path}")

    assert paths.index("/a2a/{agent_path}/agent-card.json") < catch_all_index
    assert paths.index("/a2a/{agent_path}/.well-known/agent-card.json") < catch_all_index


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


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
async def test_post_proxy_acl_denied_returns_jsonrpc_error_for_handshake(monkeypatch, method):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": method, "id": 1}),
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
    assert "result" not in body
    assert body["error"]["code"] == -32003
    assert "Access denied" in body["error"]["message"]


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


async def test_post_proxy_rejects_non_string_method(monkeypatch):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": 123, "id": 1}),
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
        acl_service=_acl_service(),
        consent_store=_consent_store(),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    # A malformed method is a protocol error, so it comes back as a JSON-RPC error, not a result.
    assert "result" not in body
    assert body["error"]["code"] == -32600
    assert "'method'" in body["error"]["message"]


async def test_post_proxy_without_server_consent_returns_url_elicitation(monkeypatch):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )
    pending_store = _PendingConsentStore()
    consent_store = _consent_store(has_server_consent=False)
    auth_context = {**_AUTH_CONTEXT, "client_id": INTERACTIVE_CLIENT_ID}

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=auth_context,
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
    assert pending == {
        "user_id": VALID_OBJECT_ID,
        "client_id": INTERACTIVE_CLIENT_ID,
        "server_path": "/github",
    }
    consent_store.has_server_consent.assert_called_once_with(
        VALID_OBJECT_ID,
        INTERACTIVE_CLIENT_ID,
        "/github",
    )


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
    # Handshake methods have a fixed result schema, so a refusal must be a JSON-RPC error — not an
    # isError-shaped result, which the client would reject as an invalid initialize/tools-list result.
    assert "result" not in body
    assert body["error"]["code"] == -32003
    assert "disabled" in body["error"]["message"].lower()
    consent_store.has_server_consent.assert_not_called()


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
async def test_post_proxy_wrong_transport_returns_jsonrpc_error_for_handshake(monkeypatch, method):
    """A non-streamable-http server reached via the direct proxy must refuse handshake methods with a
    JSON-RPC error carrying an actionable message — not an isError result the client can't validate
    as an initialize/tools-list result"""
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": method, "id": 1}),
    )

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(transport="sse")),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=_consent_store(),
        pending_store=_PendingConsentStore(),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert "result" not in body
    assert body["error"]["code"] == -32003
    # The message names the configured transport and tells the user how to fix it.
    assert "sse" in body["error"]["message"]
    assert "streamable-http" in body["error"]["message"]


async def test_post_proxy_wrong_transport_returns_iserror_result_for_tools_call(monkeypatch):
    """The same refusal on tools/call stays an isError result so the agent reads it as tool output
    (isError is a CallToolResult field, legal only for tools/call)."""
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(transport="sse")),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=_consent_store(),
        pending_store=_PendingConsentStore(),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert "sse" in body["result"]["content"][0]["text"]


async def test_post_proxy_disabled_tool_refuses_tools_call_before_forwarding(monkeypatch):
    """A tool the server owner disabled in the registry must not execute through the direct
    proxy either — the same policy the gateway's execute_tool already enforces."""
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "id": 1,
                "params": {"name": "delete_repo", "arguments": {"owner": "org"}},
            }
        ),
    )
    forward = AsyncMock(return_value=Response(status_code=500))
    monkeypatch.setattr("registry.api.proxy_routes.proxy_to_mcp_server", forward)

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(disabled_tools=["delete_repo"])),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=_consent_store(),
        pending_store=_PendingConsentStore(),
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert "delete_repo" in body["result"]["content"][0]["text"]
    assert "disabled" in body["result"]["content"][0]["text"].lower()
    forward.assert_not_called()


async def test_post_proxy_enabled_tool_still_forwards(monkeypatch):
    """tools/call for a tool that is not disabled still reaches the forwarding path."""
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "id": 1,
                "params": {"name": "list_repos", "arguments": {}},
            }
        ),
    )
    forward = AsyncMock(return_value=Response(status_code=200))
    monkeypatch.setattr("registry.api.proxy_routes.proxy_to_mcp_server", forward)

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(disabled_tools=["delete_repo"])),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=_consent_store(),
        pending_store=_PendingConsentStore(),
    )

    assert resp.status_code == 200
    forward.assert_awaited_once()


async def test_post_proxy_disabled_tools_do_not_gate_handshake_methods(monkeypatch):
    """initialize / tools/list carry no tool_name, so a disabled tool never blocks them;
    whether the proxy should also filter tools/list responses is out of scope for this gate."""
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
    )
    forward = AsyncMock(return_value=Response(status_code=200))
    monkeypatch.setattr("registry.api.proxy_routes.proxy_to_mcp_server", forward)

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(disabled_tools=["delete_repo"])),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=_consent_store(),
        pending_store=_PendingConsentStore(),
    )

    assert resp.status_code == 200
    forward.assert_awaited_once()


async def test_post_proxy_headless_agent_bypasses_server_consent(monkeypatch):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )
    consent_store = _consent_store(has_server_consent=False)
    pending_store = _PendingConsentStore()
    auth_context = {**_AUTH_CONTEXT, "client_id": settings.headless_agent_client_id}

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=auth_context,
        server_service=_server_service(_make_server(enabled=False)),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=consent_store,
        pending_store=pending_store,
    )

    body = json.loads(resp.body)
    assert "disabled" in body["result"]["content"][0]["text"].lower()
    consent_store.has_server_consent.assert_not_called()
    assert pending_store.pending == {}


async def test_post_proxy_headless_agent_still_enforces_acl(monkeypatch):
    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value={"jsonrpc": "2.0", "method": "tools/call", "id": 1}),
    )
    consent_store = _consent_store(has_server_consent=False)
    auth_context = {**_AUTH_CONTEXT, "client_id": settings.headless_agent_client_id}

    resp = await dynamic_mcp_post_proxy(
        request=_post_request(VALID_OBJECT_ID),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=auth_context,
        server_service=_server_service(_make_server()),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        acl_service=_acl_service(denied=True),
        consent_store=consent_store,
        pending_store=_PendingConsentStore(),
    )

    body = json.loads(resp.body)
    assert body["result"]["isError"] is True
    assert "Access denied" in body["result"]["content"][0]["text"]
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


@pytest.mark.parametrize(
    "client_id",
    [INTERACTIVE_CLIENT_ID, settings.headless_agent_client_id, "mcp-client-dcr"],
)
async def test_jsonrpc_proxy_gets_client_from_a2a_client_registry(monkeypatch, client_id):
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
        user_context={**_AUTH_CONTEXT, "client_id": client_id},
        a2a_agent_service=a2a_agent_service,
        acl_service=acl_service,
        a2a_client_registry=registry,
    )

    assert response.status_code == 200
    registry.get_client.assert_awaited_once_with(agent)
    forward.assert_awaited_once_with(
        request, "https://agent.example.com/a2a", proxy_client, "test-agent", is_jsonrpc=True
    )


@pytest.mark.parametrize(
    "client_id",
    [INTERACTIVE_CLIENT_ID, settings.headless_agent_client_id, "mcp-client-dcr"],
)
async def test_http_json_proxy_gets_client_from_a2a_client_registry(monkeypatch, client_id):
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
        user_context={**_AUTH_CONTEXT, "client_id": client_id},
        a2a_agent_service=a2a_agent_service,
        acl_service=acl_service,
        a2a_client_registry=registry,
    )

    assert response.status_code == 200
    registry.get_client.assert_awaited_once_with(agent)
    forward.assert_awaited_once_with(request, "https://agent.example.com/a2a/tasks/1", proxy_client, "test-agent")


def test_httpx2_decoders_supported_decoders_is_accessible():
    """
    Canary for the private-API coupling in `_HTTPX_DECODABLE_CONTENT_ENCODINGS`
    (registry/src/registry/api/proxy_routes.py).

    That constant is derived from `httpx2._decoders.SUPPORTED_DECODERS`, an underscore-prefixed
    module that is not part of httpx2's public API and could be renamed, restructured, or removed
    in a future httpx2 version without a deprecation warning. If that ever happens, we want it
    caught here as a fast, obvious CI failure -- not later as a mystifying prod bug where the
    proxy silently mis-forwards still-compressed bytes because the derived set quietly went empty
    or the import broke in some less direct way.
    """
    import httpx2

    assert frozenset(httpx2._decoders.SUPPORTED_DECODERS.keys())


def _proxy_receive():
    async def receive() -> dict:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return receive


def _proxy_post_request(extra_headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": f"/proxy/server/{VALID_OBJECT_ID}/github",
        "query_string": b"",
        "headers": [(b"accept", b"text/event-stream"), *extra_headers],
        "path_params": {"user_id": VALID_OBJECT_ID, "server_path": "github"},
    }
    return Request(scope, receive=_proxy_receive())


async def _run_proxy_and_capture(monkeypatch, msg_body: dict, extra_headers=None):
    """Drive dynamic_mcp_post_proxy through a real (mocked) downstream call.

    Returns (captured_downstream_headers, exported_spans).
    """

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(proxy_routes, "_TRACER", provider.get_tracer("test"))

    monkeypatch.setattr(
        "registry.api.proxy_routes._parse_json_rpc_body",
        AsyncMock(return_value=msg_body),
    )

    # build_authenticated_headers passes the accumulated headers straight through, so the
    # injection step is what must strip any client-supplied trace headers.
    async def fake_build(**kwargs):
        return dict(kwargs["additional_headers"])

    monkeypatch.setattr(proxy_routes, "build_authenticated_headers", AsyncMock(side_effect=fake_build))

    captured: dict = {}

    class _FakeStreamCtx:
        async def __aenter__(self):
            return SimpleNamespace(
                headers=httpx2.Headers({"content-type": "application/json"}),
                status_code=200,
                aread=AsyncMock(return_value=b'{"jsonrpc":"2.0","id":1,"result":{}}'),
            )

        async def __aexit__(self, *args):
            return False

    def fake_stream(method, url, headers=None, content=None):
        captured["headers"] = headers
        return _FakeStreamCtx()

    proxy_client = Mock()
    proxy_client.stream = fake_stream

    resp = await dynamic_mcp_post_proxy(
        request=_proxy_post_request(extra_headers or []),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(enabled=True)),
        oauth_service=Mock(),
        proxy_client=proxy_client,
        redis_client=Mock(),
        acl_service=_acl_service(),
        consent_store=_consent_store(has_server_consent=True),
    )
    assert resp.status_code == 200
    return captured["headers"], exporter.get_finished_spans()


async def test_proxy_creates_span_and_injects_fresh_trace_context(monkeypatch):
    headers, spans = await _run_proxy_and_capture(
        monkeypatch, {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "search"}, "id": 1}
    )

    assert any(s.name == "proxy.dynamic_mcp_post_proxy" for s in spans)
    assert "traceparent" in headers
    # W3C baggage percent-encodes reserved chars, so "tools/call" → "tools%2Fcall".
    assert "jarvis.mcp.method=tools%2Fcall" in headers["baggage"]
    assert "jarvis.mcp.tool_name=search" in headers["baggage"]


async def test_proxy_strips_client_supplied_trace_headers(monkeypatch):
    attacker = [
        (b"traceparent", b"00-11111111111111111111111111111111-2222222222222222-01"),
        (b"tracestate", b"attacker=1"),
        (b"baggage", b"attacker=pwned"),
    ]
    headers, _ = await _run_proxy_and_capture(
        monkeypatch,
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "search"}, "id": 1},
        extra_headers=attacker,
    )

    assert headers.get("traceparent") != "00-11111111111111111111111111111111-2222222222222222-01"
    assert "attacker" not in headers.get("baggage", "")
    assert "tracestate" not in headers


async def test_proxy_tool_name_baggage_only_for_tools_call(monkeypatch):
    headers, _ = await _run_proxy_and_capture(monkeypatch, {"jsonrpc": "2.0", "method": "initialize", "id": 1})

    assert "jarvis.mcp.method=initialize" in headers["baggage"]
    assert "jarvis.mcp.tool_name" not in headers["baggage"]


async def test_proxy_truncates_overlong_tool_name(monkeypatch):
    from registry_pkgs.telemetry.trace_propagation import MAX_BAGGAGE_VALUE_LENGTH

    long_name = "a" * (MAX_BAGGAGE_VALUE_LENGTH + 100)
    headers, _ = await _run_proxy_and_capture(
        monkeypatch,
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": long_name}, "id": 1},
    )

    injected = headers["baggage"]
    assert "jarvis.mcp.tool_name=" + "a" * MAX_BAGGAGE_VALUE_LENGTH in injected
    assert "a" * (MAX_BAGGAGE_VALUE_LENGTH + 1) not in injected


async def test_proxy_truncates_overlong_mcp_method(monkeypatch):
    from registry_pkgs.telemetry.trace_propagation import MAX_BAGGAGE_VALUE_LENGTH

    long_method = "m" * (MAX_BAGGAGE_VALUE_LENGTH + 100)
    headers, _ = await _run_proxy_and_capture(monkeypatch, {"jsonrpc": "2.0", "method": long_method, "id": 1})

    injected = headers["baggage"]
    assert "jarvis.mcp.method=" + "m" * MAX_BAGGAGE_VALUE_LENGTH in injected
    assert "m" * (MAX_BAGGAGE_VALUE_LENGTH + 1) not in injected


# --- Trace-context propagation on the GET/SSE MCP proxy and the A2A passthrough (AS-1847 follow-up) ---


def _tracer_with_exporter(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from registry.api import proxy_routes

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(proxy_routes, "_TRACER", provider.get_tracer("test"))
    return exporter


_ATTACKER_TRACE_HEADERS = [
    (b"traceparent", b"00-11111111111111111111111111111111-2222222222222222-01"),
    (b"tracestate", b"attacker=1"),
    (b"baggage", b"attacker=pwned"),
]


async def test_get_proxy_strips_client_trace_headers_and_creates_span(monkeypatch):
    import httpx2

    from registry.api import proxy_routes

    exporter = _tracer_with_exporter(monkeypatch)
    monkeypatch.setattr(
        proxy_routes, "build_authenticated_headers", AsyncMock(side_effect=lambda **kw: dict(kw["additional_headers"]))
    )

    captured: dict = {}

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace(
                headers=httpx2.Headers({"content-type": "text/event-stream"}),
                status_code=200,
                aiter_bytes=lambda: iter(()),
            )

        async def __aexit__(self, *a):
            return False

    def fake_stream(method, url, headers=None, content=None, timeout=None):
        captured["headers"] = headers
        return _Ctx()

    proxy_client = Mock()
    proxy_client.stream = fake_stream

    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": f"/proxy/server/{VALID_OBJECT_ID}/github",
        "query_string": b"",
        "headers": [(b"accept", b"text/event-stream"), *_ATTACKER_TRACE_HEADERS],
        "path_params": {"user_id": VALID_OBJECT_ID, "server_path": "github"},
    }
    request = Request(scope, receive=_proxy_receive())

    resp = await dynamic_mcp_get_proxy(
        request=request,
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(_make_server(enabled=True)),
        oauth_service=Mock(),
        proxy_client=proxy_client,
        redis_client=Mock(),
        acl_service=_acl_service(),
    )

    assert resp.status_code == 200
    headers = captured["headers"]
    assert "traceparent" in headers
    assert headers.get("traceparent") != "00-11111111111111111111111111111111-2222222222222222-01"
    assert "attacker" not in headers.get("baggage", "")
    assert "tracestate" not in headers
    assert any(s.name == "proxy.dynamic_mcp_get_proxy" for s in exporter.get_finished_spans())


async def test_forward_a2a_strips_client_trace_headers_and_creates_span(monkeypatch):
    from registry.api import proxy_routes
    from registry_pkgs.core import a2a_http

    exporter = _tracer_with_exporter(monkeypatch)

    captured: dict = {}

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace(
                headers=a2a_http.Headers({"content-type": "application/json"}),
                status_code=200,
                aread=AsyncMock(return_value=b"{}"),
            )

        async def __aexit__(self, *a):
            return False

    def fake_stream(method, url, headers=None, content=None, params=None, timeout=None):
        captured["headers"] = headers
        return _Ctx()

    proxy_client = Mock()
    proxy_client.stream = fake_stream

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": "/gateway/proxy/a2a/test-agent",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer caller-token"), *_ATTACKER_TRACE_HEADERS],
        "path_params": {"agent_path": "test-agent"},
    }
    request = Request(scope, receive=_proxy_receive())

    resp = await proxy_routes._forward_a2a(request, "https://agent.example.com/a2a", proxy_client, "test-agent")

    assert resp.status_code == 200
    headers = captured["headers"]
    assert "traceparent" in headers
    assert headers.get("traceparent") != "00-11111111111111111111111111111111-2222222222222222-01"
    assert "attacker" not in headers.get("baggage", "")
    assert "tracestate" not in headers
    assert any(s.name == "proxy.forward_a2a" for s in exporter.get_finished_spans())


# --- Downstream 401 recovery on the proxy routes (AS-1929) ---

_DOWNSTREAM_CHALLENGE = (
    'Bearer resource_metadata="https://mcp.atlassian.com/.well-known/oauth-protected-resource/v2/mcp"'
)


class _FakeDownstream:
    """proxy_client.stream stand-in that answers each send with the next queued (status, headers)."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.sent_headers: list[dict[str, str]] = []
        self.closed = 0

    def stream(self, method, url, headers=None, content=None, timeout=None):
        self.sent_headers.append(dict(headers or {}))
        status = self.statuses.pop(0)
        downstream = self
        response_headers = {"content-type": "application/json"}
        if status == 401:
            response_headers["www-authenticate"] = _DOWNSTREAM_CHALLENGE

        class _Ctx:
            async def __aenter__(self):
                return SimpleNamespace(
                    headers=httpx2.Headers(response_headers),
                    status_code=status,
                    aread=AsyncMock(return_value=b'{"jsonrpc":"2.0","id":1,"result":{}}'),
                    aiter_bytes=lambda: iter(()),
                )

            async def __aexit__(self, *args):
                downstream.closed += 1
                return False

        return _Ctx()


def _token_header_builder(monkeypatch, tokens: list[str]) -> None:
    async def build(**kwargs):
        if not tokens:
            raise UrlElicitationRequiredException("login", auth_url="https://login.example", server_name="github")
        return {**kwargs["additional_headers"], "Authorization": f"Bearer {tokens[0]}"}

    monkeypatch.setattr(proxy_routes, "build_authenticated_headers", AsyncMock(side_effect=build))


async def _proxy(downstream, server, oauth_service, method: str = "tools/call"):
    return await proxy_routes.proxy_to_mcp_server(
        call=ProxyCall.from_message(1, method, {}, b"{}", None),
        request=_proxy_post_request([]),
        target_url="https://mcp.atlassian.com/v2/mcp",
        auth_context=_AUTH_CONTEXT,
        server=server,
        oauth_service=oauth_service,
        proxy_client=downstream,
        redis_client=Mock(),
    )


@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call"])
async def test_post_proxy_401_refresh_resends_once(monkeypatch, method):
    tokens = ["t1"]
    _token_header_builder(monkeypatch, tokens)
    oauth_service = Mock()

    async def refreshed(*args, **kwargs):
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized = AsyncMock(side_effect=refreshed)
    downstream = _FakeDownstream(401, 200)

    resp = await _proxy(downstream, _make_server(auth_mode=McpAuthMode.OAUTH), oauth_service, method)

    assert resp.status_code == 200
    assert "www-authenticate" not in {k.lower() for k in resp.headers}
    assert [h["Authorization"] for h in downstream.sent_headers] == ["Bearer t1", "Bearer t2"]
    assert oauth_service.recover_from_unauthorized.await_args.kwargs == {
        "rejected_access_token": "t1",
        "www_authenticate": _DOWNSTREAM_CHALLENGE,
        "is_retry": False,
    }


@pytest.mark.parametrize("method", ["initialize", "tools/call"])
async def test_post_proxy_401_failed_refresh_returns_registry_challenge_or_elicitation(monkeypatch, method):
    tokens = ["t1"]
    _token_header_builder(monkeypatch, tokens)
    oauth_service = Mock()

    async def failed(*args, **kwargs):
        tokens.clear()
        return None

    oauth_service.recover_from_unauthorized = AsyncMock(side_effect=failed)
    downstream = _FakeDownstream(401)

    resp = await _proxy(downstream, _make_server(auth_mode=McpAuthMode.OAUTH), oauth_service, method)

    if method == "initialize":
        assert resp.status_code == 401
        challenge = resp.headers["www-authenticate"]
        assert settings.jarvis_realm in challenge
        assert "mcp.atlassian.com" not in challenge
    else:
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["error"]["data"]["elicitations"][0]["url"] == "https://login.example"
        assert "www-authenticate" not in {k.lower() for k in resp.headers}
    assert len(downstream.sent_headers) == 1


@pytest.mark.parametrize("method", ["initialize", "tools/call"])
async def test_post_proxy_second_401_loop_guard(monkeypatch, method):
    tokens = ["t1"]
    _token_header_builder(monkeypatch, tokens)
    oauth_service = Mock()
    message = "github rejected a freshly issued access token; the server's OAuth configuration may be incompatible"

    async def recover(*args, is_retry, **kwargs):
        if is_retry:
            raise DownstreamAuthRejectedException(message)
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized = AsyncMock(side_effect=recover)
    downstream = _FakeDownstream(401, 401)

    resp = await _proxy(downstream, _make_server(auth_mode=McpAuthMode.OAUTH), oauth_service, method)

    assert resp.status_code == 200
    body = json.loads(resp.body)
    # tools/call carries the refusal as an isError result; handshake methods must get a JSON-RPC error.
    if method == "tools/call":
        assert body["result"]["isError"] is True
    else:
        assert "result" not in body
        assert body["error"]["code"] == -32003
    assert message in json.dumps(body)
    assert len(downstream.sent_headers) == 2


async def test_post_proxy_second_401_changed_discovery_returns_login(monkeypatch):
    tokens = ["t1"]
    _token_header_builder(monkeypatch, tokens)
    oauth_service = Mock()

    async def recover(*args, is_retry, **kwargs):
        if is_retry:
            tokens.clear()  # changed discovery: Registry tokens deleted
            return None
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized = AsyncMock(side_effect=recover)
    downstream = _FakeDownstream(401, 401)

    resp = await _proxy(downstream, _make_server(auth_mode=McpAuthMode.OAUTH), oauth_service, "initialize")

    assert resp.status_code == 401
    assert settings.jarvis_realm in resp.headers["www-authenticate"]


@pytest.mark.parametrize("method", ["initialize", "tools/call"])
async def test_post_proxy_no_third_send(monkeypatch, method):
    tokens = ["t1"]
    _token_header_builder(monkeypatch, tokens)
    oauth_service = Mock()

    async def other_pod(*args, is_retry, **kwargs):
        tokens[0] = "t-login" if is_retry else "t2"
        return None

    oauth_service.recover_from_unauthorized = AsyncMock(side_effect=other_pod)
    downstream = _FakeDownstream(401, 401, 200)

    resp = await _proxy(downstream, _make_server(auth_mode=McpAuthMode.OAUTH), oauth_service, method)

    assert len(downstream.sent_headers) == 2
    body = json.loads(resp.body)
    if method == "tools/call":
        assert body["result"]["isError"] is True
    else:
        assert "result" not in body
        assert body["error"]["code"] == -32003
    assert "Authorization for github changed during this call; retry it." in json.dumps(body)


@pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call"])
async def test_forward_without_event_stream_accept_rejects_by_method(method):
    """A client that can't accept text/event-stream is refused before any downstream send; handshake
    methods get a JSON-RPC Invalid Request error, tools/call an isError result."""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": f"/proxy/server/{VALID_OBJECT_ID}/github",
            "query_string": b"",
            "headers": [(b"accept", b"application/json")],
        },
        receive=_proxy_receive(),
    )
    downstream = _FakeDownstream()

    resp = await proxy_routes._forward_to_downstream(
        call=ProxyCall.from_message(1, method, {}, b"{}", None),
        request=request,
        target_url="https://example.com/mcp",
        proxy_client=downstream,
        headers={},
    )

    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert downstream.sent_headers == []
    if method == "tools/call":
        assert body["result"]["isError"] is True
        assert "text/event-stream" in body["result"]["content"][0]["text"]
    else:
        assert "result" not in body
        assert body["error"]["code"] == -32600
        assert "text/event-stream" in body["error"]["message"]


async def test_post_proxy_non_oauth_401_is_relayed(monkeypatch):
    tokens = ["api-key"]
    _token_header_builder(monkeypatch, tokens)
    oauth_service = Mock()
    oauth_service.recover_from_unauthorized = AsyncMock()
    downstream = _FakeDownstream(401)

    resp = await _proxy(downstream, _make_server(), oauth_service)

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == _DOWNSTREAM_CHALLENGE
    oauth_service.recover_from_unauthorized.assert_not_awaited()


async def _get(downstream, server, oauth_service):
    return await dynamic_mcp_get_proxy(
        request=Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "http",
                "server": ("testserver", 80),
                "path": f"/proxy/server/{VALID_OBJECT_ID}/github",
                "query_string": b"",
                "headers": [(b"accept", b"text/event-stream")],
                "path_params": {"user_id": VALID_OBJECT_ID, "server_path": "github"},
            },
            receive=_proxy_receive(),
        ),
        user_id=VALID_OBJECT_ID,
        server_path="github",
        auth_context=_AUTH_CONTEXT,
        server_service=_server_service(server),
        oauth_service=oauth_service,
        proxy_client=downstream,
        redis_client=Mock(),
        acl_service=_acl_service(),
    )


async def test_get_proxy_oauth_401_deletes_rejected_token_and_returns_problem(monkeypatch):
    _token_header_builder(monkeypatch, ["t1"])
    oauth_service = Mock()
    oauth_service.token_service.delete_access_token_if_matches = AsyncMock(return_value=True)
    downstream = _FakeDownstream(401)

    resp = await _get(downstream, _make_server(auth_mode=McpAuthMode.OAUTH), oauth_service)

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert settings.jarvis_realm in resp.headers["www-authenticate"]
    assert "mcp.atlassian.com" not in resp.headers["www-authenticate"]
    oauth_service.token_service.delete_access_token_if_matches.assert_awaited_once_with(VALID_OBJECT_ID, "github", "t1")
    assert downstream.closed == 1


async def test_get_proxy_non_oauth_401_is_relayed(monkeypatch):
    _token_header_builder(monkeypatch, ["api-key"])
    oauth_service = Mock()
    oauth_service.token_service.delete_access_token_if_matches = AsyncMock()
    downstream = _FakeDownstream(401)

    resp = await _get(downstream, _make_server(), oauth_service)

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == _DOWNSTREAM_CHALLENGE
    oauth_service.token_service.delete_access_token_if_matches.assert_not_awaited()


_MODERN = "2026-07-28"
_CONSENT_KEY = UrlElicitationInputKey.CONSENT
_OAUTH_KEY = UrlElicitationInputKey.AUTHORIZATION
_CAP_KEY = "io.modelcontextprotocol/clientCapabilities"


def _direct_message(
    method: str = "tools/call",
    *,
    action: Literal["accept", "decline", "cancel"] | None = None,
    capable: bool = True,
    key: UrlElicitationInputKey = _CONSENT_KEY,
) -> dict[str, Any]:
    params = {"name": "search", "_meta": {_CAP_KEY: {"elicitation": {"url": {}}} if capable else {}}}
    if action is not None:
        params["inputResponses"] = {key: {"action": action}}
    return {"jsonrpc": "2.0", "id": "中文-id", "method": method, "params": params}


def _direct_request(message: dict[str, Any], version: str | None = _MODERN, raw: bytes | None = None) -> Request:
    body = raw if raw is not None else json.dumps(message, ensure_ascii=False).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = _proxy_post_request([])
    request.scope["headers"].extend([(b"content-length", str(len(body)).encode())])
    if version is not None:
        request.scope["headers"].append((b"mcp-protocol-version", version.encode()))
    return Request(request.scope, receive=receive)


async def _direct_run(
    monkeypatch: pytest.MonkeyPatch,
    message: dict[str, Any],
    *,
    consent: bool = False,
    version: str | None = _MODERN,
    raw: bytes | None = None,
    reauth: bool = False,
    statuses: tuple[int, ...] = (200,),
    server: SimpleNamespace | None = None,
    store: Mock | None = None,
    pending: _PendingConsentStore | Mock | None = None,
    acl: Mock | None = None,
) -> tuple[Response, list[httpx2.Request], AsyncMock, Mock, _PendingConsentStore | Mock]:
    requests = []
    statuses = iter(statuses)

    def downstream(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(next(statuses), json={"jsonrpc": "2.0", "id": message.get("id"), "result": {}})

    async def build(**kwargs: Any) -> dict[str, str]:
        if reauth:
            raise UrlElicitationRequiredException("login", auth_url="https://login.example/auth", server_name="github")
        return {**kwargs["additional_headers"], "Authorization": "Bearer downstream", "Content-Length": "9999"}

    builder = AsyncMock(side_effect=build)
    monkeypatch.setattr(proxy_routes, "build_authenticated_headers", builder)
    store = store if store is not None else _consent_store(has_server_consent=consent)
    pending = pending if pending is not None else _PendingConsentStore()
    oauth = Mock(recover_from_unauthorized=AsyncMock())
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(downstream)) as client:
        response = await dynamic_mcp_post_proxy(
            request=_direct_request(message, version, raw),
            user_id=VALID_OBJECT_ID,
            server_path="github",
            auth_context=_AUTH_CONTEXT,
            server_service=_server_service(server or _make_server()),
            oauth_service=oauth,
            proxy_client=client,
            redis_client=Mock(),
            acl_service=acl or _acl_service(),
            consent_store=store,
            pending_store=pending,
        )
    return response, requests, builder, store, pending


@pytest.mark.parametrize("method", ["tools/call", "prompts/get", "resources/read"])
@pytest.mark.parametrize("reauth", [False, True])
async def test_direct_modern_input_required_wire(monkeypatch: pytest.MonkeyPatch, method: str, reauth: bool) -> None:
    response, requests, _, _, _ = await _direct_run(monkeypatch, _direct_message(method), consent=reauth, reauth=reauth)
    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["id"] == "中文-id"
    result = body["result"]
    assert result["resultType"] == "input_required"
    assert "requestState" not in result
    key = _OAUTH_KEY if reauth else _CONSENT_KEY
    assert set(result["inputRequests"]) == {key}
    prompt = result["inputRequests"][key]
    assert prompt["method"] == "elicitation/create"
    assert prompt["params"]["mode"] == "url"
    assert "elicitationId" not in prompt["params"]
    assert ("login.example" if reauth else "/consent/server?nonce=") in prompt["params"]["url"]
    assert not requests


@pytest.mark.parametrize(
    "method",
    ["initialize", "server/discover", "tools/list", "resources/list", "resources/templates/list", "prompts/list"],
)
async def test_direct_list_and_discover_consent_exemption(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    response, requests, _, store, pending = await _direct_run(monkeypatch, _direct_message(method))
    assert response.status_code == 200
    assert len(requests) == 1
    store.has_server_consent.assert_not_called()
    assert not pending.pending


@pytest.mark.parametrize(
    "method",
    ["initialize", "server/discover", "tools/list", "resources/list", "resources/templates/list", "prompts/list"],
)
async def test_direct_reauth_method_classification(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    response, requests, _, _, _ = await _direct_run(monkeypatch, _direct_message(method), reauth=True)
    if method in {"initialize", "server/discover", "tools/list"}:
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]
    else:
        assert response.status_code == 200
        error = json.loads(response.body)["error"]
        assert error["code"] == -32003
        assert "https://login.example/auth" in error["message"]
    assert not requests


@pytest.mark.parametrize("method,capable", [("tools/call", False), ("completion/complete", True)])
async def test_direct_modern_text_fallback(monkeypatch: pytest.MonkeyPatch, method: str, capable: bool) -> None:
    response, requests, _, _, _ = await _direct_run(monkeypatch, _direct_message(method, capable=capable))
    body = json.loads(response.body)
    if method == "tools/call":
        assert body["result"]["resultType"] == "complete"
        assert body["result"]["isError"] is True
    else:
        assert body["error"]["code"] == -32003
    assert "/consent/server?nonce=" in response.body.decode()
    assert not requests


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
@pytest.mark.parametrize("consent", [False, True])
async def test_direct_retry_terminal_and_cancel_before_execution(
    monkeypatch: pytest.MonkeyPatch, action: Literal["accept", "decline", "cancel"] | None, consent: bool
) -> None:
    response, requests, builder, store, pending = await _direct_run(
        monkeypatch, _direct_message(action=action), consent=consent
    )
    if action == "accept" and consent:
        assert len(requests) == 1
        assert "inputResponses" not in json.loads(requests[0].content)["params"]
        return
    body = json.loads(response.body)
    assert body["result"]["resultType"] == "complete"
    assert body["result"]["isError"] is True
    assert not requests
    if action != "accept":
        assert "declined or cancelled" in body["result"]["content"][0]["text"]
        store.has_server_consent.assert_not_called()
        builder.assert_not_awaited()
        assert not pending.pending


async def test_direct_consent_then_oauth_prompts_under_authorization_key(monkeypatch: pytest.MonkeyPatch) -> None:
    response, requests, _, _, _ = await _direct_run(
        monkeypatch, _direct_message(action="accept"), consent=True, reauth=True
    )
    result = json.loads(response.body)["result"]
    assert result["resultType"] == "input_required"
    assert set(result["inputRequests"]) == {_OAUTH_KEY}
    assert result["inputRequests"][_OAUTH_KEY]["params"]["url"] == "https://login.example/auth"
    assert not requests


async def test_direct_oauth_retry_still_failing_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    response, requests, _, _, _ = await _direct_run(
        monkeypatch, _direct_message(action="accept", key=_OAUTH_KEY), consent=True, reauth=True
    )
    result = json.loads(response.body)["result"]
    assert result["resultType"] == "complete"
    assert "https://login.example/auth" in result["content"][0]["text"]
    assert not requests


@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_direct_oauth_decline_stops_before_consent(
    monkeypatch: pytest.MonkeyPatch, action: Literal["decline", "cancel"]
) -> None:
    response, requests, builder, store, _ = await _direct_run(
        monkeypatch, _direct_message(action=action, key=_OAUTH_KEY), consent=True, reauth=True
    )
    result = json.loads(response.body)["result"]
    assert "declined or cancelled" in result["content"][0]["text"]
    store.has_server_consent.assert_not_called()
    builder.assert_not_awaited()
    assert not requests


@pytest.mark.parametrize("version", [None, "2025-11-25", "unknown", ""])
async def test_direct_legacy_and_unknown_authorization(monkeypatch: pytest.MonkeyPatch, version: str | None) -> None:
    response, requests, _, _, _ = await _direct_run(monkeypatch, _direct_message(), version=version)
    error = json.loads(response.body)["error"]
    assert error["code"] == (-32042 if version in {None, "2025-11-25"} else -32003)
    if error["code"] == -32042:
        assert set(error["data"]["elicitations"][0]) == {"mode", "message", "url", "elicitationId"}
        UUID(error["data"]["elicitations"][0]["elicitationId"])
    assert not requests


@pytest.mark.parametrize(
    "params",
    [
        None,
        [],
        {"_meta": None},
        {"_meta": {_CAP_KEY: []}},
        {"inputResponses": []},
        {"inputResponses": {_CONSENT_KEY: None}},
        {"inputResponses": {_CONSENT_KEY: {}}},
        {"inputResponses": {_CONSENT_KEY: {"action": "invalid"}}},
        {"inputResponses": {_OAUTH_KEY: {"action": "invalid"}}},
    ],
)
async def test_direct_invalid_consumed_fields_are_protocol_errors(monkeypatch: pytest.MonkeyPatch, params: Any) -> None:
    message = _direct_message()
    message["params"] = params
    response, requests, builder, store, pending = await _direct_run(monkeypatch, message)
    assert json.loads(response.body)["error"]["code"] == -32602
    builder.assert_not_awaited()
    store.has_server_consent.assert_not_called()
    assert not requests and not pending.pending


@pytest.mark.parametrize(
    "version,answer", [(_MODERN, "accept"), (_MODERN, None), ("unknown", "accept"), ("2025-11-25", "accept")]
)
async def test_direct_preserves_downstream_fields_and_byte_lengths(
    monkeypatch: pytest.MonkeyPatch, version: str | None, answer: Literal["accept", "decline", "cancel"] | None
) -> None:
    message = _direct_message(action=answer)
    message["extra"] = {"未知": True}
    message["params"]["inputResponses"] = {
        **message["params"].get("inputResponses", {}),
        "downstream": {"action": "accept", "custom": [1, 2]},
    }
    message["params"]["requestState"] = "opaque下游state"
    raw = ("  " + json.dumps(message, ensure_ascii=False, indent=2) + "\n").encode()
    response, requests, _, _, _ = await _direct_run(monkeypatch, message, consent=True, version=version, raw=raw)
    assert response.status_code == 200
    sent = requests[0]
    assert int(sent.headers["content-length"]) == len(sent.content)
    expected = json.loads(raw)
    if version == _MODERN and answer == "accept":
        del expected["params"]["inputResponses"][_CONSENT_KEY]
        assert json.loads(sent.content) == expected
    else:
        assert sent.content == raw
    assert sent.headers["mcp-protocol-version"] == version


_REGISTRY_KEY_SETS = pytest.mark.parametrize(
    "keys",
    [(_CONSENT_KEY,), (_OAUTH_KEY,), (_CONSENT_KEY, _OAUTH_KEY)],
    ids=["consent", "authorization", "both"],
)


def _accepted_retry(keys: tuple[UrlElicitationInputKey, ...]) -> dict[str, Any]:
    message = _direct_message()
    message["params"]["inputResponses"] = {key: {"action": "accept"} for key in keys}
    return message


@_REGISTRY_KEY_SETS
async def test_direct_strips_every_registry_key(
    monkeypatch: pytest.MonkeyPatch, keys: tuple[UrlElicitationInputKey, ...]
) -> None:
    message = _accepted_retry(keys)
    message["params"]["inputResponses"]["downstream"] = {"action": "accept", "custom": [1, 2]}
    message["params"]["requestState"] = "opaque下游state"
    response, requests, _, _, _ = await _direct_run(monkeypatch, message, consent=True)
    assert response.status_code == 200
    (sent,) = requests
    expected = json.loads(json.dumps(message))
    expected["params"]["inputResponses"] = {"downstream": {"action": "accept", "custom": [1, 2]}}
    assert json.loads(sent.content) == expected
    assert int(sent.headers["content-length"]) == len(sent.content)


@_REGISTRY_KEY_SETS
async def test_direct_strip_drops_empty_input_responses(
    monkeypatch: pytest.MonkeyPatch, keys: tuple[UrlElicitationInputKey, ...]
) -> None:
    _, requests, _, _, _ = await _direct_run(monkeypatch, _accepted_retry(keys), consent=True)
    (sent,) = requests
    assert "inputResponses" not in json.loads(sent.content)["params"]
    assert int(sent.headers["content-length"]) == len(sent.content)


@_REGISTRY_KEY_SETS
async def test_direct_oauth_resend_reuses_clean_body_and_computed_length(
    monkeypatch: pytest.MonkeyPatch, keys: tuple[UrlElicitationInputKey, ...]
) -> None:
    message = _accepted_retry(keys)
    _, requests, builder, _, _ = await _direct_run(
        monkeypatch, message, consent=True, statuses=(401, 200), server=_make_server(auth_mode=McpAuthMode.OAUTH)
    )
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert "inputResponses" not in json.loads(requests[1].content)["params"]
    assert all(int(r.headers["content-length"]) == len(r.content) for r in requests)
    assert builder.await_count == 2


@pytest.mark.parametrize("operation", ["check", "save"])
async def test_direct_redis_failure_is_internal_error(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    from redis.exceptions import RedisError

    store = _consent_store(has_server_consent=False)
    pending = Mock()
    if operation == "check":
        store.has_server_consent.side_effect = RedisError("private data")
    else:
        pending.save.side_effect = RedisError("private data")
    response, requests, _, _, _ = await _direct_run(monkeypatch, _direct_message(), store=store, pending=pending)
    assert json.loads(response.body)["error"] == {"code": -32603, "message": "Internal server error"}
    assert not requests


@pytest.mark.parametrize("operation", ["check", "save"])
async def test_direct_sync_consent_io_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    import asyncio
    import threading

    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def blocking(*args: Any) -> bool:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "test failed to release storage operation"
        return False

    store = _consent_store(has_server_consent=False)
    pending = _PendingConsentStore()
    if operation == "check":
        store.has_server_consent.side_effect = blocking
    else:
        pending.save = blocking
    task = asyncio.create_task(_direct_run(monkeypatch, _direct_message(), store=store, pending=pending))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not task.done()
    finally:
        release.set()
        await task


@pytest.mark.parametrize("capabilities", [None, {"elicitation": {"form": {}}}])
async def test_direct_missing_or_form_only_capabilities_use_text(
    monkeypatch: pytest.MonkeyPatch, capabilities: dict[str, Any] | None
) -> None:
    message = _direct_message()
    if capabilities is None:
        del message["params"]["_meta"][_CAP_KEY]
    else:
        message["params"]["_meta"][_CAP_KEY] = capabilities
    response, _, _, _, _ = await _direct_run(monkeypatch, message)
    assert json.loads(response.body)["result"]["resultType"] == "complete"


async def test_direct_other_downstream_answer_is_not_registry_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _direct_message()
    message["params"]["inputResponses"] = {"downstream": {"action": "accept"}}
    response, _, _, _, _ = await _direct_run(monkeypatch, message)
    assert json.loads(response.body)["result"]["resultType"] == "input_required"


@pytest.mark.parametrize("method", ["prompts/get", "resources/read", "completion/complete"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
async def test_direct_non_tool_retry_uses_jsonrpc_error(
    monkeypatch: pytest.MonkeyPatch, method: str, action: Literal["accept", "decline", "cancel"] | None
) -> None:
    response, requests, _, _, _ = await _direct_run(monkeypatch, _direct_message(method, action=action))
    assert json.loads(response.body)["error"]["code"] == -32003
    assert not requests


@pytest.mark.parametrize("method", ["server/discover", "resources/list", "tools/call"])
async def test_direct_consent_exempt_and_accept_still_enforce_acl(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    response, requests, builder, store, _ = await _direct_run(
        monkeypatch, _direct_message(method, action="accept"), consent=True, acl=_acl_service(denied=True)
    )
    assert "Access denied" in response.body.decode()
    assert not requests
    builder.assert_not_awaited()
    store.has_server_consent.assert_not_called()


@pytest.mark.parametrize("server", [_make_server(enabled=False), _make_server(transport="stdio")])
async def test_direct_accept_does_not_bypass_server_configuration(
    monkeypatch: pytest.MonkeyPatch, server: SimpleNamespace | None
) -> None:
    response, requests, builder, _, _ = await _direct_run(
        monkeypatch, _direct_message(action="accept"), consent=True, server=server
    )
    assert json.loads(response.body)["result"]["isError"] is True
    assert not requests
    builder.assert_not_awaited()


async def test_direct_legacy_reauth_preserves_url_elicitation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = "c7ce6a46-0147-4021-abaa-6d2a54f8357c"
    parse = Mock(return_value=expected)
    monkeypatch.setattr(proxy_routes, "parse_elicitation_id", parse)
    response, _, _, _, _ = await _direct_run(
        monkeypatch, _direct_message(), consent=True, reauth=True, version="2025-11-25"
    )
    assert json.loads(response.body)["error"]["data"]["elicitations"][0]["elicitationId"] == expected
    parse.assert_called_once_with("https://login.example/auth")


@pytest.mark.parametrize("streaming", [False, True])
async def test_direct_downstream_input_result_passthrough_and_close(streaming: bool) -> None:
    payload = b'{ "jsonrpc":"2.0","id":1,"result":{"resultType":"input_required", "requestState":"opaque", "inputRequests":{"downstream":{"method":"elicitation/create","params":{"mode":"url","url":"https://example.com","message":"approve"}}}} }'
    wire = b"data: " + payload + b"\n\n" if streaming else payload

    class BodyStream(httpx2.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield wire[:20]
            yield wire[20:]

        async def aclose(self) -> None:
            self.closed = True

    stream = BodyStream()

    def downstream(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream" if streaming else "application/json"}, stream=stream
        )

    message = _direct_message()
    request = _direct_request(message)
    call = ProxyCall.from_message(1, "tools/call", message, await request.body(), _MODERN)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(downstream)) as client:
        response = await proxy_routes._forward_to_downstream(
            call=call, request=request, target_url="https://example.com/mcp", proxy_client=client, headers={}
        )
        if streaming:
            assert not stream.closed
            content = b"".join([chunk async for chunk in response.body_iterator])
            assert "content-length" not in response.headers
        else:
            content = response.body
        assert content == wire
        assert stream.closed


async def test_direct_client_notification_keeps_local_202(monkeypatch: pytest.MonkeyPatch) -> None:
    message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    response, requests, builder, _, _ = await _direct_run(monkeypatch, message)
    assert response.status_code == 202
    assert not response.body and not requests
    builder.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
async def test_forward_a2a_uses_own_decoder_set(monkeypatch: pytest.MonkeyPatch, streaming: bool) -> None:
    from registry.api import proxy_routes
    from registry_pkgs.core import a2a_http

    coding = "gzip"
    assert coding in a2a_http.DECODABLE_CONTENT_ENCODINGS
    monkeypatch.setattr(proxy_routes, "_HTTPX_DECODABLE_CONTENT_ENCODINGS", frozenset())
    headers = a2a_http.Headers(
        {
            "content-type": "text/event-stream" if streaming else "application/json",
            "content-encoding": coding,
        }
    )

    async def chunks():
        yield b"{}"

    backend = SimpleNamespace(headers=headers, status_code=200, aread=AsyncMock(return_value=b"{}"), aiter_bytes=chunks)
    context = AsyncMock()
    context.__aenter__.return_value = backend
    client = Mock()
    client.stream.return_value = context
    request = Request(
        {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []}, receive=_proxy_receive()
    )

    response = await proxy_routes._forward_a2a(request, "https://agent.example.com", client, "test-agent")

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    if streaming:
        assert b"".join([chunk async for chunk in response.body_iterator]) == b"{}"
    else:
        assert response.body == b"{}"
    context.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("coding,removed", [("GZip, deflate", True), ("unknown", False), ("gzip, unknown", False)])
def test_encoding_header_requires_all_supported_codings(coding: str, removed: bool) -> None:
    from registry.api import proxy_routes
    from registry_pkgs.core import a2a_http

    headers = a2a_http.Headers({"content-encoding": coding})
    outgoing = dict(headers)
    proxy_routes._pop_content_encoding_if_fully_decoded(outgoing, headers, a2a_http.DECODABLE_CONTENT_ENCODINGS)
    assert ("content-encoding" not in outgoing) is removed


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("can_decode", [False, True])
async def test_forward_mcp_uses_own_decoder_set(
    monkeypatch: pytest.MonkeyPatch, streaming: bool, can_decode: bool
) -> None:
    import gzip

    from registry.core.proxy_call import ProxyCall
    from registry_pkgs.core import a2a_http

    # Opposite capabilities ensure either MCP call site fails if wired to A2A.
    supported = frozenset({"gzip"})
    monkeypatch.setattr(proxy_routes, "_HTTPX_DECODABLE_CONTENT_ENCODINGS", supported if can_decode else frozenset())
    monkeypatch.setattr(a2a_http, "DECODABLE_CONTENT_ENCODINGS", frozenset() if can_decode else supported)
    plain_body = b"data: {}\n\n" if streaming else b"{}"
    body = plain_body if can_decode else gzip.compress(plain_body)

    async def chunks() -> AsyncIterator[bytes]:
        yield body

    backend = SimpleNamespace(
        headers=httpx2.Headers(
            {
                "content-type": "text/event-stream" if streaming else "application/json",
                "content-encoding": "gzip",
            }
        ),
        status_code=200,
        aread=AsyncMock(return_value=body),
        aiter_bytes=chunks,
    )
    context = AsyncMock()
    context.__aenter__.return_value = backend
    client = Mock()
    client.stream.return_value = context
    proxy_call = ProxyCall.from_message(1, "tools/list", {}, b"{}", None)

    response = await proxy_routes._forward_to_downstream(
        call=proxy_call,
        request=_proxy_post_request([]),
        target_url="https://mcp.example.com",
        proxy_client=client,
        headers={},
    )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == (None if can_decode else "gzip")
    if streaming:
        assert b"".join([chunk async for chunk in response.body_iterator]) == body
    else:
        assert response.body == body
    context.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("can_decode", [False, True])
async def test_get_proxy_strips_content_encoding_only_when_decoded(
    monkeypatch: pytest.MonkeyPatch, can_decode: bool
) -> None:
    from registry_pkgs.core import a2a_http

    _token_header_builder(monkeypatch, ["api-key"])
    # Opposite capabilities ensure the GET path fails if wired to the A2A decoder set.
    supported = frozenset({"gzip"})
    monkeypatch.setattr(proxy_routes, "_HTTPX_DECODABLE_CONTENT_ENCODINGS", supported if can_decode else frozenset())
    monkeypatch.setattr(a2a_http, "DECODABLE_CONTENT_ENCODINGS", frozenset() if can_decode else supported)
    body = b"data: {}\n\n"

    async def chunks() -> AsyncIterator[bytes]:
        yield body

    backend = SimpleNamespace(
        headers=httpx2.Headers({"content-type": "text/event-stream", "content-encoding": "gzip"}),
        status_code=200,
        aiter_bytes=chunks,
    )
    context = AsyncMock()
    context.__aenter__.return_value = backend
    client = Mock()
    client.stream.return_value = context

    response = await _get(client, _make_server(), Mock())

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == (None if can_decode else "gzip")
    assert b"".join([chunk async for chunk in response.body_iterator]) == body
    context.__aexit__.assert_awaited_once()
