from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from registry.mcpgw.tools.search import _run_search
from registry_pkgs.core.exceptions import InternalServerException


def _make_ctx(search_entities: AsyncMock) -> MagicMock:
    lifespan_context = SimpleNamespace(search_service=SimpleNamespace(search_entities=search_entities))
    request_state = SimpleNamespace(user={"user_id": "user-probe"})
    request_context = SimpleNamespace(
        lifespan_context=lifespan_context,
        request=SimpleNamespace(state=request_state),
    )
    ctx = MagicMock()
    ctx.request_context = request_context
    return ctx


@pytest.mark.asyncio
async def test_run_search_other_error_reports_generic() -> None:
    search_entities = AsyncMock(side_effect=RuntimeError("boom"))
    ctx = _make_ctx(search_entities)

    with pytest.raises(InternalServerException) as excinfo:
        await _run_search(ctx, "q", 3, "hybrid", ["tool"], "mcp")

    assert "entity discovery failed" in str(excinfo.value)


@pytest.mark.asyncio
async def test_run_search_success_returns_results() -> None:
    search_entities = AsyncMock(return_value={"results": [{"entity_type": "tool"}]})
    ctx = _make_ctx(search_entities)

    result = await _run_search(ctx, "q", 3, "hybrid", ["tool"], "mcp")

    assert result == [{"entity_type": "tool"}]


# ---------------------------------------------------------------------------
# End-to-end: a raised exception from the tool becomes CallToolResult(isError=True)
# via the real FastMCP -> lowlevel-server conversion.
# ---------------------------------------------------------------------------


async def _call_tool_e2e(tool_name: str, search_entities: AsyncMock):
    import mcp.types as types
    from mcp.server.fastmcp import FastMCP
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    from registry.mcpgw.tools import search as search_tools

    mcp = FastMCP("test-mcpgw-search")
    for name, fn in search_tools.get_tools():
        mcp.tool(name=name)(fn)
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]

    lifespan = SimpleNamespace(search_service=SimpleNamespace(search_entities=search_entities))
    request = SimpleNamespace(state=SimpleNamespace(user={"user_id": "user-probe"}))
    rc = RequestContext(
        request_id=1,
        meta=None,
        session=MagicMock(),
        lifespan_context=lifespan,
        experimental=None,
        request=request,
        close_sse_stream=None,
        close_standalone_sse_stream=None,
    )
    token = request_ctx.set(rc)
    try:
        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name=tool_name,
                arguments={"query": "x", "top_n": 3, "type_list": ["tool"]},
            ),
        )
        return (await handler(req)).root
    finally:
        request_ctx.reset(token)


@pytest.mark.asyncio
async def test_discover_tool_reports_generic_error_for_other_failures():
    search_entities = AsyncMock(side_effect=RuntimeError("boom"))

    result = await _call_tool_e2e("discover_servers", search_entities)

    assert result.isError is True
    assert "entity discovery failed" in result.content[0].text
