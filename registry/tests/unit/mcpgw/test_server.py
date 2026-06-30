from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from registry.mcpgw.tools import server

# Module where the metrics context managers look up the domain record functions.
DECORATORS_PATH = "registry.core.telemetry_decorators"


def _patch_server_service(monkeypatch, server_obj):
    """Stub _get_server_service so get_server_by_id returns the given server (or None)."""
    fake_service = SimpleNamespace(get_server_by_id=AsyncMock(return_value=server_obj))
    monkeypatch.setattr(server, "_get_server_service", lambda ctx: fake_service)
    # record_server_request hits the real metrics client; neutralize it for unit tests.
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
        # execute_tool_impl reads ctx.request_context.request.state.user up front.
        state = SimpleNamespace(user={"username": "alice", "user_id": "u1"})
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
