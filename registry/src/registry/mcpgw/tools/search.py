import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import Field

from ...api.v1.search_routes import SearchRequest, search_servers_impl
from ...auth.dependencies import UserContextDict
from ...core.exceptions import InternalServerException
from ..core.types import McpAppContext

logger = logging.getLogger(__name__)


async def discover_servers_impl(
    ctx: Context[ServerSession, McpAppContext],
    query: str,
    top_n: int | None = None,
    search_type: str = "hybrid",
    type_list: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    🔍 Discover available MCP tools, resources, or prompts via vector search.

    All entity types go through the same vector path. Every result contains
    tool_name and server_id directly, ready for execute_tool.

    Args:
        query: Natural language description or keywords to search
               (e.g., "github", "search engines", "database tools")
        top_n: Maximum number of results to return. Defaults to 3 if not specified.
        search_type: Search strategy:
                    - "hybrid" (default): Combines semantic + keyword for best accuracy
                    - "near_text": Pure semantic/vector search (best for concept matching)
                    - "bm25": Pure keyword search (best for exact term matching)
                    - "similarity_store": Alternative similarity algorithm
        type_list: Entity types to search for (default: ["tool"]):
                  - ["tool"]: Returns individual tools (each doc embeds full server context)
                  - ["resource"]: Returns resources (each doc embeds full server context)
                  - ["prompt"]: Returns prompts (each doc embeds full server context)
                  - Mix types: ["tool", "resource"] for multiple entity types
        ctx: FastMCP context with user auth

    Returns:
        List of matching entities, each with tool_name and server_id ready for execute_tool.

    Raises:
        InternalServerException: On any runtime exception
    """
    if type_list is None:
        type_list = ["tool"]

    if top_n is None:
        top_n = 3

    logger.info(f"🔍 Discovering {type_list} for query: '{query}' (search_type={search_type})")

    try:
        payload: dict[str, Any] = {"search_type": search_type, "type_list": type_list}
        if query:
            payload["query"] = query
        if top_n is not None:
            payload["top_n"] = top_n

        search_request = SearchRequest.model_validate(payload)
        user_context: UserContextDict = ctx.request_context.request.state.user  # type: ignore[union-attr]

        lifespan_context = ctx.request_context.lifespan_context
        result = await search_servers_impl(
            search_request,
            user_context,
            mcp_server_repo=lifespan_context.mcp_server_repo,
        )

        servers = result.get("servers", [])
        total = result.get("total", 0)

        logger.info(f"✅ Discovered {total} result(s) for query: '{query}'")

        return servers

    except Exception:
        logger.exception("Server discovery failed")

        raise InternalServerException("server discovery failed")


# ============================================================================
# Tool Factory Functions for Registration
# ============================================================================


def get_tools() -> list[tuple[str, Callable]]:
    """
    Export tools for registration in server.py.

    Returns:
        List of (tool_name, tool_function) tuples ready for registration
    """

    async def discover_servers(
        ctx: Context[ServerSession, McpAppContext],
        query: Annotated[
            str,
            Field(
                min_length=0,
                max_length=512,
                description="Natural language query or keywords (e.g., 'web search', 'github', 'email automation'). May be omitted or empty for listing.",
            ),
        ] = "",
        top_n: Annotated[
            int | None,
            Field(
                description="Max results to return. Defaults to 3 if not specified.",
            ),
        ] = None,
        search_type: Annotated[
            str,
            Field(
                description="Search strategy: 'hybrid' (best), 'near_text' (semantic), 'bm25' (keyword), 'similarity_store' (alternative)",
            ),
        ] = "hybrid",
        type_list: Annotated[
            list[str],
            Field(
                description="Entity types to search: ['tool'] (default), ['resource'], ['prompt'], or mix multiple types e.g. ['tool', 'resource']",
            ),
        ] = Field(
            default_factory=lambda: ["tool"],
        ),
    ) -> list[dict[str, Any]]:
        """
        🔍 AUTO-USE: Discover tools, resources, or prompts from MCP servers.

        **Use this search order by default:**
        1. `type_list=["tool"]` for action-oriented tasks (default, most efficient)
        2. `type_list=["resource"]` or `type_list=["prompt"]` when the user needs those specifically

        **What each type means:**
        - `["tool"]`: best default for search, API calls, automation, or data operations
        - `["resource"]`: for reading URIs, cached data, or file-like resources
        - `["prompt"]`: for reusable prompt workflows

        Each result doc embeds full server context (server name, path, title, description)
        in its content — no separate server lookup is needed.

        **Search strategies:**
        - `hybrid`: best default, combines semantic and keyword search
        - `near_text`: semantic/concept matching
        - `bm25`: exact keyword matching
        - `similarity_store`: alternative retrieval path

        **How to use results:**
        Every result in the returned array contains `tool_name` and `server_id` directly.
        Pass them unchanged to `execute_tool` — no further lookup or name translation needed.

        **Examples:**
        - News or web search: `discover_servers(query="web search news", type_list=["tool"])`
        - GitHub operations: `discover_servers(query="github repositories", type_list=["tool"])`
        - Cached data: `discover_servers(query="cached data", type_list=["resource"])`

        **Execution:**
        If discovery returns `{"tool_name": "tavily_search", "server_id": "abc123", ...}`,
        call `execute_tool(tool_name="tavily_search", server_id="abc123", arguments={...})`.

        Use `read_resource(server_id, resource_uri)` for resources.
        Use `execute_prompt(server_id, prompt_name, arguments)` for prompts.
        """
        return await discover_servers_impl(ctx, query, top_n, search_type, type_list)

    return [
        ("discover_servers", discover_servers),
    ]
