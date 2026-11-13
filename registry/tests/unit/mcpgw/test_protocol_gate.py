"""Tests for ModernProtocolGate: the 2026-07-28 kill switch in front of the gateway."""

import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

from registry.core.config import settings
from registry.mcpgw import create_gateway_mcp_app
from registry.mcpgw import server as gateway
from registry.mcpgw.core.protocol_gate import ModernProtocolGate

_GATEWAY_DEPS = (
    "a2a_client_registry",
    "server_service",
    "mcp_server_repo",
    "a2a_agent_repo",
    "search_service",
    "mcp_client_service",
    "oauth_service",
    "client_info_store",
    "redis_client",
    "acl_service",
    "consent_store",
    "pending_consent_store",
)


class _InnerApp:
    """Minimal ASGI app that records whether it was reached and answers 200."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        # Drain the body so the gate's replay (if any) is exercised.
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _drive(gate, *, method="POST", protocol_version=None, body=b""):
    headers = []
    if protocol_version is not None:
        headers.append((b"mcp-protocol-version", protocol_version.encode()))
    scope = {"type": "http", "method": method, "headers": headers}

    chunks = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    await gate(scope, receive, send)
    return sent


def _response(sent):
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    try:
        payload = json.loads(body) if body else None
    except json.JSONDecodeError:
        payload = None
    return start["status"], payload


@pytest.mark.asyncio
async def test_disabled_rejects_modern_era_with_unsupported_protocol_error():
    inner = _InnerApp()
    gate = ModernProtocolGate(inner, lambda: False)

    body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "server/discover"}).encode()
    sent = await _drive(gate, protocol_version="2026-07-28", body=body)

    status, payload = _response(sent)
    assert inner.called is False
    assert status == 400
    assert payload["id"] == 7
    assert payload["error"]["code"] == -32022
    assert payload["error"]["data"]["supported"] == list(HANDSHAKE_PROTOCOL_VERSIONS)
    assert payload["error"]["data"]["requested"] == "2026-07-28"


@pytest.mark.asyncio
async def test_disabled_rejection_uses_null_id_when_body_has_none():
    inner = _InnerApp()
    gate = ModernProtocolGate(inner, lambda: False)

    sent = await _drive(gate, protocol_version="2026-07-28", body=b"")
    _status, payload = _response(sent)
    assert payload["id"] is None
    assert inner.called is False


@pytest.mark.asyncio
async def test_disabled_rejection_bounds_oversized_body():
    """An oversized modern-era body is rejected without buffering it all; the truncated prefix
    doesn't parse, so the id falls back to null."""
    inner = _InnerApp()
    gate = ModernProtocolGate(inner, lambda: False)

    big = b'{"jsonrpc":"2.0","id":7,"method":"x","params":{"pad":"' + b"A" * 200_000 + b'"}}'
    sent = await _drive(gate, protocol_version="2026-07-28", body=big)

    status, payload = _response(sent)
    assert inner.called is False
    assert status == 400
    assert payload["error"]["code"] == -32022
    assert payload["id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol_version", ["2025-11-25", None])
async def test_disabled_passes_handshake_and_headerless_requests(protocol_version):
    inner = _InnerApp()
    gate = ModernProtocolGate(inner, lambda: False)

    sent = await _drive(gate, protocol_version=protocol_version, body=b"{}")
    status, _ = _response(sent)
    assert inner.called is True
    assert status == 200


@pytest.mark.asyncio
async def test_enabled_passes_modern_era_through():
    inner = _InnerApp()
    gate = ModernProtocolGate(inner, lambda: True)

    sent = await _drive(gate, protocol_version="2026-07-28", body=b"{}")
    status, _ = _response(sent)
    assert inner.called is True
    assert status == 200


@pytest.mark.asyncio
async def test_sdk_auto_client_falls_back_to_2025_11_25_against_gated_app(monkeypatch):
    """End-to-end: with the gate off, an SDK Client(mode="auto") probes the modern era, is rejected with
    the handshake -32022, and falls back to initialize — negotiating 2025-11-25, listing tools, and
    receiving the gateway instructions."""
    # TestClient/ASGITransport use a non-production Host; the DNS-rebinding guard is orthogonal here.
    monkeypatch.setattr(settings, "mcpgw_enable_dns_rebinding_protection", False)

    def _reject(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected outgoing request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_reject)) as proxy_client:
        container = SimpleNamespace(mcpgw_proxy_client=proxy_client, **{name: object() for name in _GATEWAY_DEPS})
        app = create_gateway_mcp_app(container_provider=lambda: container)
        streamable = app.streamable_http_app(
            stateless_http=True,
            json_response=False,
            transport_security=gateway.build_transport_security_settings(),
        )
        gated = ModernProtocolGate(streamable, lambda: settings.mcpgw_modern_protocol_enabled)

        async with app.session_manager.run():
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=gated), base_url="http://localhost"
            ) as http_client:
                async with Client(
                    streamable_http_client("http://localhost/mcp", http_client=http_client), mode="auto"
                ) as client:
                    assert client.protocol_version == "2025-11-25"
                    assert client.instructions == gateway._SYSTEM_INSTRUCTIONS
                    tools = await client.list_tools()
                    assert {t.name for t in tools.tools} == {
                        "discover_servers",
                        "discover_agents",
                        "execute_agent",
                        "execute_tool",
                        "read_resource",
                        "execute_prompt",
                    }
