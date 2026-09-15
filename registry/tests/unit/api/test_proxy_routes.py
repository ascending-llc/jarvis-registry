"""Tests for ObjectId validation in the dynamic proxy GET/POST handlers.

The spec required updating proxy-route assertions for the new {user_id}/{server_path} URL
format. These tests verify that both handlers reject non-ObjectId user_id values with a
400 and a ``{"detail": ...}`` body before any further processing occurs.
"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
from uuid import UUID

import httpx
import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
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
from registry.core.config import settings
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.models.a2a_agent import NoSupportedTransportError
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.testing.federation_metadata import make_azure_foundry_metadata

VALID_OBJECT_ID = "507f1f77bcf86cd799439011"
INVALID_USER_IDS = ["mcpgw", "not-an-objectid", "123", ""]

_AUTH_CONTEXT = {
    "auth_method": "bearer",
    "user_id": VALID_OBJECT_ID,
    "username": "test",
    "client_id": INTERACTIVE_CLIENT_ID,
}


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
    assert "error" not in body
    assert "disabled" in body["result"]["content"][0]["text"].lower()
    consent_store.has_server_consent.assert_not_called()


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
                headers=httpx.Headers({"content-type": "application/json"}),
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
    import httpx

    from registry.api import proxy_routes

    exporter = _tracer_with_exporter(monkeypatch)
    monkeypatch.setattr(
        proxy_routes, "build_authenticated_headers", AsyncMock(side_effect=lambda **kw: dict(kw["additional_headers"]))
    )

    captured: dict = {}

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace(
                headers=httpx.Headers({"content-type": "text/event-stream"}),
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
    import httpx

    from registry.api import proxy_routes

    exporter = _tracer_with_exporter(monkeypatch)

    captured: dict = {}

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace(
                headers=httpx.Headers({"content-type": "application/json"}),
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
