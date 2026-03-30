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
    🔍 Discover available MCP servers, tools, resources, or prompts.

    This flexible search tool can find specific entity types (tools, resources, prompts)
    or full servers with all their capabilities. Use type_list to control what's returned.

    Args:
        query: Natural language description or keywords to search
               (e.g., "github", "search engines", "database tools")
        top_n: Maximum number of results to return.
              If None, auto-sets to 3 for tools/resources/prompts, 1 for servers
        search_type: Search strategy:
                    - "hybrid" (default): Combines semantic + keyword for best accuracy
                    - "near_text": Pure semantic/vector search (best for concept matching)
                    - "bm25": Pure keyword search (best for exact term matching)
                    - "similarity_store": Alternative similarity algorithm
        type_list: Entity types to search for (default: ["tool"]):
                  - ["tool"]: Returns only tools (most token-efficient)
                  - ["resource"]: Returns only resources
                  - ["prompt"]: Returns only prompts
                  - ["server"]: Returns full servers with all capabilities (most tokens)
                  - Mix types: ["tool", "resource"] for multiple entity types
        ctx: FastMCP context with user auth

    Returns:
        List of matching entities based on type_list parameter

    Raises:
        InternalServerException: On any runtime exception
    """
    if type_list is None:
        type_list = ["tool"]

    # Smart defaults for top_n based on entity type
    if top_n is None:
        # For specific entity types (tool/resource/prompt), return more results (3)
        # For full servers, return fewer (1) due to token cost
        if "server" in type_list and len(type_list) == 1:
            top_n = 1  # Servers are token-heavy, return only 1
        else:
            top_n = 3  # Tools/resources/prompts are lightweight, return 3

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
            server_service=lifespan_context.server_service,
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
                description="Natural language query or keywords (e.g., 'web search', 'github', 'email automation'). May be omitted or empty. For `type_list=[\"server\"]`, an empty query means list available servers.",
            ),
        ] = "",
        top_n: Annotated[
            int | None,
            Field(
                description="Max results to return. Auto-sets to 3 for tools/resources/prompts, 1 for servers if not specified",
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
                description="Entity types to search: ['tool'] (most efficient, default), ['resource'], ['prompt'], ['server'] (full details, most tokens), or mix multiple types",
            ),
        ] = Field(
            default_factory=lambda: ["tool"],
        ),
    ) -> list[dict[str, Any]]:
        """
        🔍 AUTO-USE: Discover tools, resources, prompts, or full server documents.

        **Use this search order by default:**
        1. `type_list=["tool"]` first for executable tools
        2. `type_list=["resource"]` or `type_list=["prompt"]` when the user needs those specifically
        3. `type_list=["server"]` only when you need a full Mongo-style server document

        **What each type means:**
        - `["tool"]`: best default for action-oriented tasks such as search, API calls, automation, or data operations
        - `["resource"]`: for reading URIs, cached data, or file-like resources
        - `["prompt"]`: for reusable prompt workflows
        - `["server"]`: for full server configs, including `config.toolFunctions`, resources, and prompts

        **Search strategies:**
        - `hybrid`: best default, combines semantic and keyword search
        - `near_text`: semantic/concept matching
        - `bm25`: exact keyword matching
        - `similarity_store`: alternative retrieval path

        **How to interpret the returned `servers` array:**
        - Treat results as full server documents only when `type_list` is exactly `["server"]`.
        - In every other case, including `type_list=["tool"]`, treat each result as a directly usable discovery result for execution.

        **If `type_list` is exactly `["server"]`:**
        - Each item in `servers` is a full server document in MongoDB format.
        - To execute a tool from that server:
          1. Inspect the `$.config.toolFunctions` field of the server document.
          2. Choose one tool entry whose description and parameters match the user's task
          3. Set the `server_id` parameter of the `execute_tool` call to that server document's `id`
          4. Set the `tool_name` parameter of the `execute_tool` call to that chosen tool entry's `mcpToolName`
          5. Only if `mcpToolName` is missing, fall back to that chosen tool entry's key/name

        **In every other case, including `type_list=["tool"]`:**
        - Each result is already an executable match.
        - Use the returned `tool_name` unchanged as `execute_tool.tool_name`.
        - Use the returned `server_id` unchanged as `execute_tool.server_id`.

        **Examples:**
        - News or web search: `discover_servers(query="web search news", type_list=["tool"])`
        - GitHub operations: `discover_servers(query="github repositories", type_list=["tool"])`
        - Cached data: `discover_servers(query="cached data", type_list=["resource"])`
        - Full capability inspection: `discover_servers(query="github", type_list=["server"])`

        **Execution examples:**
        - Tool result:
          - If discovery returns `{"tool_name": "tavily_search", "server_id": "abc123", ...}`,
            call `execute_tool(tool_name="tavily_search", server_id="abc123", arguments={...})`.
        - Server result:
          - If `$.config.toolFunctions["add_numbers_mcp_minimal_mcp_iam"].mcpToolName = "add_numbers"`,
            call `execute_tool(tool_name="add_numbers", server_id="<server id>", arguments={...})`.

        Use `read_resource(server_id, resource_uri)` for resources.
        Use `execute_prompt(server_id, prompt_name, arguments)` for prompts.
        """
        return await discover_servers_impl(ctx, query, top_n, search_type, type_list)

    return [
        ("discover_servers", discover_servers),
    ]
