"""Real SDK negotiation/retry through the production direct-connect app and auth middleware."""

import json
from typing import Any, Literal
from unittest.mock import AsyncMock

import httpx2
import pytest
from mcp import Client
from mcp.client.session import ClientRequestContext
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ElicitRequestParams, ElicitResult, TextContent

from registry.api import proxy_routes
from registry.constants import UrlElicitationInputKey
from tests.integration.test_mcpgw_stateless import (
    USER_ID,
    _container_stub,
    _FakeRedis,
    _make_pod,
    _server_doc,
)
from tests.integration.test_mcpgw_stateless import (
    auth_headers as auth_headers,
)

_PATH = f"/proxy/server/{USER_ID}/github"
_MODERN = "2026-07-28"


async def _sdk_flow(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],  # noqa: F811
    *,
    action: Literal["accept", "decline", "cancel"] | None = "accept",
    grant: bool = False,
    modern: bool = True,
) -> tuple[str, CallToolResult | None, list[dict[str, Any]], list[dict[str, Any]], list[ElicitRequestParams]]:
    sent: list[dict[str, Any]] = []
    upstream: list[dict[str, Any]] = []
    prompts: list[ElicitRequestParams] = []

    def downstream(request: httpx2.Request) -> httpx2.Response:
        message = json.loads(request.content)
        sent.append(message)
        method = message["method"]
        result: dict[str, Any]
        if method == "server/discover":
            if not modern:
                return httpx2.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    },
                )
            result = {
                "supportedVersions": [_MODERN],
                "capabilities": {"tools": {}},
                "resultType": "complete",
                "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "test-downstream", "version": "1"}},
            }
        elif method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-downstream", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "search", "inputSchema": {"type": "object"}}], "resultType": "complete"}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "done"}], "resultType": "complete"}
        else:
            raise AssertionError(method)
        if modern and method in {"server/discover", "tools/list"}:
            result.update(cacheScope="private", ttlMs=0)
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    async def build(**kwargs: Any) -> dict[str, str]:
        return kwargs["additional_headers"]

    monkeypatch.setattr(proxy_routes, "build_authenticated_headers", build)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(downstream)) as proxy_client:
        container = _container_stub(_FakeRedis(), has_consent=False)
        container.mcp_proxy_client = proxy_client
        container.server_service.extract_server_path = AsyncMock(return_value="/github")
        server = _server_doc()
        server.config["enabled"] = True
        container.server_service.get_server_by_path = AsyncMock(return_value=server)
        container.acl_service.check_user_permission = AsyncMock()

        async def answer(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
            del context
            assert action is not None
            prompts.append(params)
            assert params.mode == "url"
            assert "/consent/server?nonce=" in params.url
            if grant:
                container.consent_store.has_server_consent.return_value = True
            return ElicitResult(action=action)

        async def record(request: httpx2.Request) -> None:
            if request.method == "POST":
                upstream.append(json.loads(await request.aread()))

        app = _make_pod(container)
        app.state.container = container
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost",
                headers=auth_headers,
                event_hooks={"request": [record]},
            ) as http_client:
                async with Client(
                    streamable_http_client(f"http://localhost{_PATH}", http_client=http_client),
                    mode="auto",
                    elicitation_callback=answer if action is not None else None,
                ) as client:
                    version = client.protocol_version
                    assert version is not None
                    await client.list_tools()
                    if not modern:
                        # Legacy authorization is still the old error; negotiation itself must succeed.
                        from mcp.shared.exceptions import MCPError

                        with pytest.raises(MCPError) as exc:
                            await client.call_tool("search", {})
                        assert exc.value.code == -32042
                        tool_result = None
                    else:
                        response = await client.call_tool("search", {})
                        assert isinstance(response, CallToolResult)
                        tool_result = response
    return version, tool_result, sent, upstream, prompts


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
async def test_direct_sdk_auto_negotiation_and_authorization(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],  # noqa: F811
    action: Literal["accept", "decline", "cancel"] | None,
    grant: bool,
) -> None:
    version, result, sent, upstream, prompts = await _sdk_flow(monkeypatch, auth_headers, action=action, grant=grant)
    assert version == _MODERN
    assert result is not None
    assert "server/discover" in [message["method"] for message in sent]
    assert "initialize" not in [message["method"] for message in upstream]
    calls = [message for message in upstream if message["method"] == "tools/call"]
    assert len(prompts) == (0 if action is None else 1)
    assert len(calls) == (1 if action is None else 2)
    downstream_calls = [message for message in sent if message["method"] == "tools/call"]
    if action == "accept" and grant:
        assert not result.is_error
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "done"
        assert len(downstream_calls) == 1
        assert "inputResponses" not in downstream_calls[0]["params"]
    else:
        assert result.is_error
        assert not downstream_calls
    if len(calls) == 2:
        assert calls[1]["params"]["inputResponses"][UrlElicitationInputKey.CONSENT]["action"] == action
        assert "requestState" not in calls[1]["params"]


async def test_direct_sdk_auto_falls_back_for_legacy_downstream(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],  # noqa: F811
) -> None:
    version, _, sent, _, _ = await _sdk_flow(monkeypatch, auth_headers, modern=False)
    assert version == "2025-11-25"
    assert [message["method"] for message in sent][:2] == ["server/discover", "initialize"]
