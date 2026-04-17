import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import Field

from ...api.v1.search_routes import SearchRequest, search_entities_impl
from ...auth.dependencies import UserContextDict
from ...core.exceptions import InternalServerException
from ..core.types import McpAppContext

logger = logging.getLogger(__name__)


async def discover_entities_impl(
    ctx: Context[ServerSession, McpAppContext],
    query: str,
    top_n: int | None = None,
    search_type: str = "hybrid",
    type_list: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    🔍 Discover available MCP tools, resources, or prompts via vector search.

    All entity types go through the same vector path.

    Results include entity-specific identifiers directly from the vector store:
    - tools use `tool_name`
    - resources use `resource_uri`
    - prompts use `prompt_name`

    Each result also includes `server_id`, so no MongoDB lookup is required during discovery.

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
        List of matching entity results, each containing the execution-ready identifier
        for its entity type plus server_id.

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
        result = await search_entities_impl(
            search_request,
            user_context,
            mcp_server_repo=lifespan_context.mcp_server_repo,
        )

        servers = result.get("results", [])
        total = result.get("total", 0)

        logger.info(f"✅ Discovered {total} result(s) for query: '{query}'")

        return servers

    except Exception:
        logger.exception("Entity discovery failed")

        raise InternalServerException("entity discovery failed")


# ============================================================================
# Tool Factory Functions for Registration
# ============================================================================


def get_tools() -> list[tuple[str, Callable]]:
    """
    Export tools for registration in server.py.

    Returns:
        List of (tool_name, tool_function) tuples ready for registration
    """

    async def discover_entities(
        ctx: Context[ServerSession, McpAppContext],
        query: Annotated[
            str,
            Field(
                min_length=0,
                max_length=512,
                description="Keywords or natural language describing what you need (e.g., 'web search', 'github', 'send email').",
            ),
        ] = "",
        top_n: Annotated[
            int | None,
            Field(description="Max results to return. Defaults to 3."),
        ] = None,
        type_list: Annotated[
            list[str],
            Field(description="Entity types: 'tool' (default), 'resource', or 'prompt'."),
        ] = Field(
            default_factory=lambda: ["tool"],
        ),
    ) -> list[dict[str, Any]]:
        """Find tools, resources, or prompts matching the query.
        Returns {confidence, results[]} where confidence is high/low/ambiguous/none.
        Execute: tool→execute_tool(tool_name, server_id, arguments), resource→read_resource(server_id, resource_uri), prompt→execute_prompt(server_id, prompt_name, arguments)."""
        return await discover_entities_impl(ctx, query, top_n, "hybrid", type_list)

    return [
        ("discover_entities", discover_entities),
    ]
