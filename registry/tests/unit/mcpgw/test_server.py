from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.mcpgw.tools import server
from registry.mcpgw.tools.server import execute_tool_impl


class _PermissiveAccessibleSet:
    """Mock-friendly container that allows every `<id> in self` ACL check."""

    def __contains__(self, _item: object) -> bool:
        return True


def _make_ctx(
    *,
    user_id: str | None = "507f1f77bcf86cd799439011",
    accessible_server_ids: list[str] | None = None,
    include_user_context: bool = True,
):
    accessible: object = _PermissiveAccessibleSet() if accessible_server_ids is None else accessible_server_ids
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=accessible)

    server_service = MagicMock()
    server_service.get_server_by_id = AsyncMock()

    lifespan_context = SimpleNamespace(
        acl_service=acl_service,
        server_service=server_service,
        mcp_client_service=MagicMock(),
        oauth_service=MagicMock(),
        redis_client=MagicMock(),
    )
    request_state = SimpleNamespace()
    if include_user_context:
        request_state.user = {"user_id": user_id, "username": "test"}
    request_context = SimpleNamespace(
        lifespan_context=lifespan_context,
        request=SimpleNamespace(state=request_state),
    )

    ctx = MagicMock()
    ctx.request_context = request_context
    ctx.session = SimpleNamespace(client_params=None)
    ctx.request_id = "req-1"
    return ctx


def _make_server(server_id: str | None = None):
    oid = PydanticObjectId(server_id) if server_id else PydanticObjectId()
    return SimpleNamespace(
        id=oid,
        path="/github",
        serverName="github",
        config={
            "enabled": True,
            "requiresInit": False,
            "type": "streamable-http",
            "url": "https://example.com/mcp",
        },
    )


@pytest.mark.asyncio
async def test_execute_tool_wrapper_uses_tool_name_as_downstream_name(monkeypatch):
    execute_tool = dict(server.get_tools())["execute_tool"]
    execute_mock = AsyncMock(return_value=SimpleNamespace(result="ok"))
    monkeypatch.setattr(server, "execute_tool_impl", execute_mock)

    ctx = SimpleNamespace()
    arguments = {"query": "ai"}

    await execute_tool(
        ctx=ctx,
        tool_name="tavily_search",
        arguments=arguments,
        server_id="server-123",
    )

    execute_mock.assert_awaited_once_with(ctx, "tavily_search", arguments, "server-123")


@pytest.mark.asyncio
async def test_execute_tool_impl_missing_user_context_returns_auth_error():
    ctx = _make_ctx(include_user_context=False)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, str(PydanticObjectId()))

    assert result.isError is True
    assert "Authentication required" in result.content[0].text
    ctx.request_context.lifespan_context.server_service.get_server_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_invalid_user_id_returns_auth_error():
    ctx = _make_ctx(user_id="not-an-objectid")

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, str(PydanticObjectId()))

    assert result.isError is True
    assert "Authentication required" in result.content[0].text
    ctx.request_context.lifespan_context.server_service.get_server_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_no_server_returns_error():
    ctx = _make_ctx()
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = None

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, str(PydanticObjectId()))

    assert result.isError is True
    assert "no server" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_execute_tool_impl_acl_denied_returns_error(monkeypatch):
    server_id = str(PydanticObjectId())
    other_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[other_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.isError is True
    assert "Access denied" in result.content[0].text
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_acl_allowed_proceeds(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(
        server,
        "_downstream_tool_call",
        AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "ok"}]}}),
    )

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.isError
