import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import Field

from ...api.v1.search_routes import SearchRequest, search_servers_impl
from ...auth.dependencies import UserContextDict
from ...core.exceptions import InternalServerException
from ...schemas.discovery import DomainResult, ServerCapabilities
from ...services.discovery_service import get_server_capabilities as _get_capabilities
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
    Semantic search across all registered MCP tools, resources, or prompts.

    Search path: Weaviate hybrid (BM25 + vector) → FlashRank reranker → top_n results.
    Each result is self-contained (server context embedded in content) — no MongoDB
    lookup needed. tool_name and server_id are ready for execute_tool directly.

    Args:
        query: Natural language description or keywords to search.
        top_n: Maximum number of results to return. Defaults to 3.
        search_type: "hybrid" (default) | "near_text" | "bm25" | "similarity_store"
        type_list: ["tool"] (default) | ["resource"] | ["prompt"] | mix
        ctx: FastMCP context with user auth
    """
    if type_list is None:
        type_list = ["tool"]
    if top_n is None:
        top_n = 3

    logger.info("🔍 Discovering %s for query: '%s' (search_type=%s)", type_list, query, search_type)

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

        logger.info("✅ Discovered %d result(s) for query: '%s'", total, query)
        return servers

    except Exception:
        logger.exception("Server discovery failed")
        raise InternalServerException("server discovery failed")


# ============================================================================
# Tool Factory Functions for Registration
# ============================================================================


async def get_server_capabilities_impl(
    server_name: str,
    entity_types: list[str] | None = None,
) -> ServerCapabilities | None:
    """
    Level 2: Fetch tool/resource/prompt summaries for a server from MongoDB.

    Returns names + one-line descriptions only — no parameter schemas.
    """
    caps = await _get_capabilities(server_name=server_name)
    if caps is None:
        return None

    # Filter entity types if requested
    if entity_types:
        if "tool" not in entity_types:
            caps = caps.model_copy(update={"tools": []})
        if "resource" not in entity_types:
            caps = caps.model_copy(update={"resources": []})
        if "prompt" not in entity_types:
            caps = caps.model_copy(update={"prompts": []})

    return caps


async def discover_domains_impl(
    ctx: Context[ServerSession, McpAppContext],
    query: str = "",
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """
    Aggregate MCP servers and A2A agents by domain (tags).

    Fetches server_summary docs from Weaviate, groups by tag, returns
    domain cards with representative server names and tool counts.

    Args:
        query: Optional query to filter domains by relevance.
        top_n: Maximum number of domains to return. Defaults to 10.
        ctx: FastMCP context.
    """
    logger.info("🗂️ Discovering domains (query='%s')", query)

    try:
        lifespan_context = ctx.request_context.lifespan_context
        mcp_repo = lifespan_context.mcp_server_repo
        a2a_repo = lifespan_context.a2a_agent_repo

        # --- MCP: fetch server_summary docs ---
        mcp_filters: dict[str, Any] = {"entity_type": "server_summary", "enabled": True}
        if query:
            server_docs = await mcp_repo.asearch_with_rerank(
                query=query,
                k=100,
                candidate_k=200,
                filters=mcp_filters,
            )
        else:
            server_docs = await mcp_repo.afilter(filters=mcp_filters, limit=500)

        # --- A2A: fetch agent-level docs ---
        # TODO:
        a2a_filters: dict[str, Any] = {"entity_type": "agent", "is_enabled": True}
        if query:
            agent_docs = await a2a_repo.asearch_with_rerank(
                query=query,
                k=50,
                candidate_k=100,
                filters=a2a_filters,
            )
        else:
            agent_docs = await a2a_repo.afilter(filters=a2a_filters, limit=200)

        # --- Build domain map from server_summary docs ---
        # tag → {server_names, total_tools}
        domain_servers: dict[str, list[str]] = defaultdict(list)
        domain_tools: dict[str, int] = defaultdict(int)

        for doc in server_docs:
            tags: list[str] = doc.get("tags") or []
            server_name: str = doc.get("server_name", "")
            num_tools: int = doc.get("num_tools") or 0
            for tag in tags:
                if server_name and server_name not in domain_servers[tag]:
                    domain_servers[tag].append(server_name)
                    domain_tools[tag] += num_tools

        # --- Add A2A agents to domain map ---
        domain_agents: dict[str, list[str]] = defaultdict(list)
        domain_skills: dict[str, int] = defaultdict(int)

        for doc in agent_docs:
            tags = doc.get("tags") or []
            agent_name: str = doc.get("agent_name", "")
            for tag in tags:
                if agent_name and agent_name not in domain_agents[tag]:
                    domain_agents[tag].append(agent_name)
                    domain_skills[tag] += 1

        # --- Combine all tags ---
        all_tags = set(domain_servers.keys()) | set(domain_agents.keys())
        if not all_tags:
            logger.info("No domain tags found")
            return []

        # Sort by total capability count (tools + skills), descending
        def _domain_score(tag: str) -> int:
            return domain_tools[tag] + domain_skills[tag]

        sorted_tags = sorted(all_tags, key=_domain_score, reverse=True)[:top_n]

        domains: list[dict[str, Any]] = []
        for tag in sorted_tags:
            servers = domain_servers[tag]
            agents = domain_agents[tag]
            total_tools = domain_tools[tag]
            total_skills = domain_skills[tag]

            # Build description: "github, gitlab — code management. 12 tools, 2 agents."
            representative = ", ".join((servers + agents)[:3])
            description = f"{tag}: {representative}" if representative else tag

            result = DomainResult(
                domain=tag,
                description=description,
                server_names=servers,
                agent_names=agents,
                total_tools=total_tools,
                total_skills=total_skills,
                relevance_score=0.0,
            )
            domains.append(result.model_dump())

        logger.info("✅ Discovered %d domain(s)", len(domains))
        return domains

    except Exception:
        logger.exception("Domain discovery failed")
        raise InternalServerException("domain discovery failed")


# ---------------------------------------------------------------------------
# Tool Factory
# ---------------------------------------------------------------------------


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
                description=(
                    "Natural language query or keywords "
                    "(e.g. 'web search news', 'github', 'email automation'). "
                    "May be empty for listing."
                ),
            ),
        ] = "",
        top_n: Annotated[
            int | None,
            Field(description="Max results to return. Defaults to 3 if not specified."),
        ] = None,
        search_type: Annotated[
            str,
            Field(
                description=(
                    "Search strategy: 'hybrid' (best), 'near_text' (semantic), "
                    "'bm25' (keyword), 'similarity_store' (alternative)"
                ),
            ),
        ] = "hybrid",
        type_list: Annotated[
            list[str],
            Field(
                description=(
                    "Entity types to search: ['tool'] (default), "
                    "['resource'], ['prompt'], or mix e.g. ['tool','resource']"
                ),
            ),
        ] = Field(default_factory=lambda: ["tool"]),
    ) -> list[dict[str, Any]]:
        """
        Search for MCP tools, resources, or prompts by natural language query.

        Each result contains:
          server_id   — use directly in execute_tool
          server_name — use in get_server_capabilities if you need the full tool list
          tool_name   — use directly in execute_tool
          content     — "server_name | path | description | tool_name | tool_description"

        If tool_name and its description in content match the user's intent,
        call execute_tool immediately using server_id + tool_name from this result.
        If results are ambiguous, call get_server_capabilities(server_name=...) next.

        Example result:
          {"server_id": "6978e12b529328946c13297c", "server_name": "tavily-search",
           "tool_name": "tavily_search",
           "content": "tavily-search | /internet | Web search ... | tavily_search | Search the web ..."}
        """
        return await discover_servers_impl(ctx, query, top_n, search_type, type_list)

    async def get_server_capabilities(
        ctx: Context[ServerSession, McpAppContext],
        server_name: Annotated[
            str,
            Field(
                description=(
                    "Server name as returned by discover_servers (server_name field) "
                    "or discover_domains (server_names list). E.g. 'github', 'slack'."
                ),
            ),
        ],
        entity_types: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Which entity types to return. "
                    "Default: all. Options: ['tool'], ['resource'], ['prompt'] or any mix."
                ),
            ),
        ] = None,
    ) -> dict[str, Any] | None:
        """
        Get the complete tool/resource/prompt list for a specific server.

        Use when:
          - User explicitly names a server (e.g. "用 tavily", "use tavily")
          - discover_servers results are ambiguous and you need to see all tools

        Returns tool names + descriptions only (no parameter schemas).
        The server_id in the response is ready for execute_tool.

        Example:
          get_server_capabilities(server_name="tavily-search")
          → {server_id: "6978e12b529328946c13297c",
             tools: [{name: "tavily_search", description: "Search the web..."},
                     {name: "tavily_extract", description: "Extract content from URLs..."},
                     {name: "tavily_crawl",   description: "Crawl a website..."}]}
          → execute_tool(server_id="6978e12b529328946c13297c",
                         tool_name="tavily_extract", arguments={"urls": [...]})
        """
        caps = await get_server_capabilities_impl(server_name, entity_types)
        if caps is None:
            return None
        return caps.model_dump()

    async def discover_domains(
        ctx: Context[ServerSession, McpAppContext],
        query: Annotated[
            str,
            Field(
                min_length=0,
                max_length=512,
                description=(
                    "Optional query to filter domains by relevance. Leave empty to list all available domains."
                ),
            ),
        ] = "",
        top_n: Annotated[
            int,
            Field(description="Max number of domains to return. Defaults to 10."),
        ] = 10,
    ) -> list[dict[str, Any]]:
        """
        List available capability domains (tag-based groupings of servers ).

        Use as a last resort when:
          - discover_servers returns nothing useful
          - User asks "what can you do?" or wants to browse categories

        Each result contains:
          domain       — tag key (e.g. 'internet', 'github', 'aws')
          server_names — MCP servers in this domain (use with get_server_capabilities)
          total_tools  — total MCP tools available in this domain

        Example result:
          {"domain": "internet", "server_names": ["tavily-search", "brave-search"],"total_tools": 11}
        """
        return await discover_domains_impl(ctx, query, top_n)

    return [
        ("discover_servers", discover_servers),
        ("get_server_capabilities", get_server_capabilities),
        ("discover_domains", discover_domains),
    ]
