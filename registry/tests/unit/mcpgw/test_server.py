from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from registry.mcpgw.tools import server


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
