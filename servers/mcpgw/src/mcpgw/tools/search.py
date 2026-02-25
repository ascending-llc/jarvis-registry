import logging
from collections.abc import Callable
from typing import Any

from fastmcp import Context
from pydantic import Field

from ..config import settings
from ..core.registry import call_registry_api

logger = logging.getLogger(__name__)


async def discover_servers_impl(
    ctx: Context,
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
        # Use centralized registry API call with automatic auth header extraction
        result = await call_registry_api(
            ctx,
            method="POST",
            endpoint=f"/api/{settings.API_VERSION}/search/servers",
            payload={"query": query, "top_n": top_n, "search_type": search_type, "type_list": type_list},
        )

        servers = result.get("servers", [])
        total = result.get("total", 0)

        logger.info(f"✅ Discovered {total} result(s) for query: '{query}'")

        return servers

    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        raise Exception(f"Discovery failed: {str(e)}")


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
        ctx: Context,
        query: str = Field(
            "",
            description="Natural language query or keywords (e.g., 'web search', 'github', 'email automation') - leave empty to see all",
        ),
        top_n: int | None = Field(
            None,
            description="Max results to return. Auto-sets to 3 for tools/resources/prompts, 1 for servers if not specified",
        ),
        search_type: str = Field(
            "hybrid",
            description="Search strategy: 'hybrid' (best), 'near_text' (semantic), 'bm25' (keyword), 'similarity_store' (alternative)",
        ),
        type_list: list[str] = Field(
            default_factory=lambda: ["tool"],
            description="Entity types to search: ['tool'] (most efficient, default), ['resource'], ['prompt'], ['server'] (full details, most tokens), or mix multiple types",
        ),
    ) -> list[dict[str, Any]]:
        """
        🔍 AUTO-USE: Unified discovery for tools, resources, prompts, or servers using smart semantic search.

        **⚡ TOKEN OPTIMIZATION STRATEGY (CRITICAL):**

        **ALWAYS follow this search sequence to minimize tokens:**
        1. **First try:** type_list=["tool"] (DEFAULT) - Returns 3 tools (~100 tokens each)
        2. **If no tools found, try:** type_list=["resource"] or type_list=["prompt"] - Returns 3 results each
        3. **Last resort:** type_list=["server"] - Returns 1 full server config (1000+ tokens)

        **Smart Defaults:**
        - type_list=["tool"] (default): Returns top 3 tools
        - type_list=["resource"]: Returns top 3 resources
        - type_list=["prompt"]: Returns top 3 prompts
        - type_list=["server"]: Returns top 1 server (token-heavy)

        **Why this matters:**
        - type_list=["tool"]: Returns just executable tools with input schemas (efficient ✅)
        - type_list=["resource"]: Returns just data sources/URIs (efficient ✅)
        - type_list=["prompt"]: Returns just prompt templates (efficient ✅)
        - type_list=["server"]: Returns EVERYTHING - all tools, resources, prompts (expensive ❌)

        **When to use each type:**

        🔧 **type_list=["tool"]** (DEFAULT - most common)
        - User needs to DO something: search web, get data, create/update/delete
        - Action-oriented requests: "find news", "search GitHub", "send email"
        - Example queries: "web search", "github operations", "database queries"
        - Returns: tool_name, server_path, description, input_schema

        📚 **type_list=["resource"]**
        - User needs to ACCESS data sources, caches, or URIs
        - Data retrieval: "read cached results", "get configuration", "access files"
        - Example queries: "cached search results", "config files", "data exports"
        - Returns: resource URIs and descriptions

        💬 **type_list=["prompt"]**
        - User needs pre-configured workflows or expert guidance
        - Complex workflows: "research assistant", "fact checker", "code reviewer"
        - Example queries: "research workflow", "analysis templates"
        - Returns: prompt templates with argument schemas

        🖥️ **type_list=["server"]** (use sparingly - high token cost)
        - User asks "what can you do?" or wants to explore ALL capabilities
        - Need complete server catalog with all tools/resources/prompts
        - Failed to find specific tools and need full context
        - Example: "show me all github capabilities" (not "search github repos")
        - Returns: FULL server configs (expensive - use only when necessary)

        **Search Type Strategies:**
        - **"hybrid"** (default): Best overall accuracy, combines semantic + keyword + AI reranking
        - **"near_text"**: Pure semantic for concept matching ("find info online" → search engines)
        - **"bm25"**: Pure keyword for exact terms ("github" → finds "github" literally)
        - **"similarity_store"**: Alternative algorithm if others fail

        **Real-World Examples:**

        ✅ GOOD (token-efficient):
        ```
        # User: "search for Donald Trump news"
        discover_servers(query="web search news", type_list=["tool"])  # Returns tavily_search tool only

        # User: "list GitHub repos"
        discover_servers(query="github repositories", type_list=["tool"])  # Returns list_repos tool

        # User: "get cached results"
        discover_servers(query="cached data", type_list=["resource"])  # Returns resource URIs
        ```

        ❌ BAD (token-wasteful):
        ```
        # User: "search for news"
        discover_servers(query="news", type_list=["server"])  # Returns ENTIRE server configs (wasteful!)
        ```

        **Fallback Strategy:**
        ```
        # Try tools first (efficient, returns 3 tools by default)
        results = discover_servers(query="github operations", type_list=["tool"])
        if not results:
            # Try resources if applicable
            results = discover_servers(query="github operations", type_list=["resource"])
        if not results:
            # Last resort: full server (returns 1 server by default)
            results = discover_servers(query="github operations", type_list=["server"])
        ```

        **Returns:**
        - For type_list=["tool"]: {tool_name, server_path, description, input_schema, server_id}
        - For type_list=["resource"]: {resource_uri, description, server_path}
        - For type_list=["prompt"]: {prompt_name, description, arguments, server_path}
        - For type_list=["server"]: {serverName, path, config{tools, resources, prompts}, tags, numTools}

        **After discovery, use:**
        - execute_tool(server_path, tool_name, arguments) for tools
        - read_resource(server_id, resource_uri) for resources
        - execute_prompt(server_id, prompt_name, arguments) for prompts
        """
        return await discover_servers_impl(ctx, query, top_n, search_type, type_list)

    return [
        ("discover_servers", discover_servers),
    ]
