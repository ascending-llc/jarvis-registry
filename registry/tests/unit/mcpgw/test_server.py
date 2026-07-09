from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.core.exceptions import InternalServerException
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
        session_store=MagicMock(),
        consent_store=MagicMock(),
        pending_consent_store=MagicMock(),
    )
    lifespan_context.consent_store.has_server_consent.return_value = True
    request_state = SimpleNamespace()
    if include_user_context:
        request_state.user = {"user_id": user_id, "username": "test", "client_id": "claude"}
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


# Module where the metrics context managers look up the domain record functions.
DECORATORS_PATH = "registry.core.telemetry_decorators"


def _patch_server_service(monkeypatch, server_obj):
    """Stub _get_server_service so get_server_by_id returns the given server (or None)."""
    fake_service = SimpleNamespace(get_server_by_id=AsyncMock(return_value=server_obj))
    monkeypatch.setattr(server, "_get_server_service", lambda ctx: fake_service)
    # record_server_request hits the real metrics client; neutralize it for unit tests.
    monkeypatch.setattr(server, "record_server_request", lambda *a, **k: None)


def _patch_server_service_raises(monkeypatch, exc: Exception):
    """Stub _get_server_service so get_server_by_id raises before `server` is ever assigned."""
    fake_service = SimpleNamespace(get_server_by_id=AsyncMock(side_effect=exc))
    monkeypatch.setattr(server, "_get_server_service", lambda ctx: fake_service)
    monkeypatch.setattr(server, "record_server_request", lambda *a, **k: None)


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


@pytest.mark.unit
@pytest.mark.metrics
class TestExecutionMetricsWiring:
    """The metrics context managers are wired into the FastMCP execution impls."""

    def _tool_ctx(self):
        # execute_tool_impl reads ctx.request_context.request.state.user up front
        # and now validates user_id as an ObjectId, so use a valid one.
        state = SimpleNamespace(user={"username": "alice", "user_id": "507f1f77bcf86cd799439011"})
        request = SimpleNamespace(state=state)
        request_context = SimpleNamespace(request=request)
        return SimpleNamespace(request_context=request_context)

    @pytest.mark.asyncio
    async def test_execute_tool_impl_records_server_not_found(self, monkeypatch):
        _patch_server_service(monkeypatch, None)
        with patch(f"{DECORATORS_PATH}._record_tool_execution") as mock_record:
            result = await server.execute_tool_impl(
                ctx=self._tool_ctx(),
                tool_name="tavily_search",
                arguments={},
                server_id="missing",
            )

        assert result.isError is True
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["tool_name"] == "tavily_search"
        assert call_kwargs["success"] is False
        assert call_kwargs["error_type"] == "server_not_found"

    @pytest.mark.asyncio
    async def test_read_resource_impl_records_success(self, monkeypatch):
        server_obj = SimpleNamespace(serverName="docs-server", path="/docs")
        _patch_server_service(monkeypatch, server_obj)
        with patch(f"{DECORATORS_PATH}._record_resource_access") as mock_record:
            await server.read_resource_impl(
                user_context={"username": "alice"},
                server_id="s1",
                resource_uri="tavily://search-results/AI",
                ctx=SimpleNamespace(),
            )

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["server_name"] == "docs-server"
        assert call_kwargs["success"] is True
        assert call_kwargs["error_type"] == "none"
        # Unbounded resource_uri must never become a metric label.
        assert "resource_uri" not in call_kwargs

    @pytest.mark.asyncio
    async def test_read_resource_impl_records_server_not_found(self, monkeypatch):
        _patch_server_service(monkeypatch, None)
        with patch(f"{DECORATORS_PATH}._record_resource_access") as mock_record:
            result = await server.read_resource_impl(
                user_context={"username": "alice"},
                server_id="missing",
                resource_uri="tavily://x",
                ctx=SimpleNamespace(),
            )

        assert result.isError is True
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["success"] is False
        assert call_kwargs["error_type"] == "server_not_found"

    @pytest.mark.asyncio
    async def test_read_resource_impl_get_server_by_id_raises_returns_internal_error(self, monkeypatch):
        """Regression test for [m1]: `server` must be bound before get_server_by_id can raise,
        otherwise the except block's `if server is not None` crashes with UnboundLocalError."""
        _patch_server_service_raises(monkeypatch, RuntimeError("db unavailable"))
        with patch(f"{DECORATORS_PATH}._record_resource_access"), pytest.raises(InternalServerException):
            await server.read_resource_impl(
                user_context={"username": "alice"},
                server_id="s1",
                resource_uri="tavily://x",
                ctx=SimpleNamespace(),
            )

    @pytest.mark.asyncio
    async def test_execute_prompt_impl_records_success(self, monkeypatch):
        server_obj = SimpleNamespace(serverName="prompt-server", path="/prompts")
        _patch_server_service(monkeypatch, server_obj)
        with patch(f"{DECORATORS_PATH}._record_prompt_execution") as mock_record:
            await server.execute_prompt_impl(
                user_context={"username": "alice"},
                server_id="s1",
                prompt_name="research_assistant",
                arguments={"topic": "AI"},
                ctx=SimpleNamespace(),
            )

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["prompt_name"] == "research_assistant"
        assert call_kwargs["server_name"] == "prompt-server"
        assert call_kwargs["success"] is True
        assert call_kwargs["error_type"] == "none"

    @pytest.mark.asyncio
    async def test_execute_prompt_impl_get_server_by_id_raises_returns_internal_error(self, monkeypatch):
        """Regression test for [m1]: `server` must be bound before get_server_by_id can raise,
        otherwise the except block's `if server is not None` crashes with UnboundLocalError."""
        _patch_server_service_raises(monkeypatch, RuntimeError("db unavailable"))
        with patch(f"{DECORATORS_PATH}._record_prompt_execution"), pytest.raises(InternalServerException):
            await server.execute_prompt_impl(
                user_context={"username": "alice"},
                server_id="s1",
                prompt_name="research_assistant",
                arguments={"topic": "AI"},
                ctx=SimpleNamespace(),
            )


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
async def test_execute_tool_impl_acl_runtime_error_returns_retryable_error(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx()
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.lifespan_context.acl_service.get_accessible_resource_ids.side_effect = RuntimeError(
        "acl unavailable"
    )
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.isError is True
    assert "Service temporarily unavailable" in result.content[0].text
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_without_server_consent_returns_elicitation_fallback(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.lifespan_context.consent_store.has_server_consent.return_value = False
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.isError is True
    assert "explicitly consent" in result.content[0].text
    assert "/consent/server?nonce=" in result.content[0].text
    ctx.request_context.lifespan_context.pending_consent_store.save.assert_called_once()
    _, pending = ctx.request_context.lifespan_context.pending_consent_store.save.call_args.args
    assert pending == {"user_id": "507f1f77bcf86cd799439011", "client_id": "claude", "server_path": "/github"}
    ctx.request_context.lifespan_context.consent_store.has_server_consent.assert_called_once_with(
        "507f1f77bcf86cd799439011",
        "claude",
        "/github",
    )
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_without_server_consent_raises_url_elicitation(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.lifespan_context.consent_store.has_server_consent.return_value = False
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)
    monkeypatch.setattr(server, "_support_url_elicitation", lambda _client_params: True)

    with pytest.raises(server.UrlElicitationRequiredError):
        await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    ctx.request_context.lifespan_context.pending_consent_store.save.assert_called_once()
    ctx.request_context.lifespan_context.consent_store.has_server_consent.assert_called_once_with(
        "507f1f77bcf86cd799439011",
        "claude",
        "/github",
    )
    ctx.request_context.lifespan_context.session_store.append.assert_called_once()
    elicitation_id, saved_session = ctx.request_context.lifespan_context.session_store.append.call_args.args
    assert elicitation_id
    assert saved_session is ctx.session
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


@pytest.mark.asyncio
async def test_execute_tool_impl_downstream_tool_error_records_error_type(monkeypatch):
    """Regression test for [m3]: a downstream tool-level failure (isError=True) must be
    recorded with a non-default error_type, not silently left as "none"."""
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(
        server,
        "_downstream_tool_call",
        AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "boom"}], "isError": True}}),
    )

    with patch(f"{DECORATORS_PATH}._record_tool_execution") as mock_record:
        result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.isError is True
    call_kwargs = mock_record.call_args[1]
    assert call_kwargs["success"] is False
    assert call_kwargs["error_type"] == "downstream_tool_error"
