from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from httpx import AsyncClient, Limits, Timeout
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..core.config import settings
from .core.event_store import InMemoryEventStore
from .core.types import McpAppContext
from .tools import proxied, search

if TYPE_CHECKING:
    from ..container import RegistryContainer

_SYSTEM_INSTRUCTIONS = """This MCP Gateway provides unified access to registered MCP servers through centralized discovery and execution.

═══════════════════════════════════════════════════════════════════
TOOL OVERVIEW
═══════════════════════════════════════════════════════════════════

MCP Tools (action-oriented, immediate results):
  discover_servers          Semantic search across all registered MCP tools/resources/prompts.
                            Each result contains: server_id, server_name, tool_name, content.
                            Use this FIRST for any MCP request — unless the user names a server.
  get_server_capabilities   Given a server_name, returns its complete tool list
                            (names + descriptions, no parameter schemas).
                            Use when: (a) user explicitly names a server, or
                                      (b) discover_servers returns tools that don't match intent.
  execute_tool              Execute a tool. Requires: server_id, tool_name, arguments.
  read_resource             Read a resource. Requires: server_id, resource_uri.
  execute_prompt            Run a prompt. Requires: server_id, prompt_name.

═══════════════════════════════════════════════════════════
RESULT SHAPES
═══════════════════════════════════════════════════════════════════

discover_servers result item:
  {
    "server_id":   "6978e12b529328946c13297c",   ← use in execute_tool
    "server_name": "tavily-search",               ← use in get_server_capabilities
    "tool_name":   "tavily_search",               ← use in execute_tool
    "content":     "tavily-search | /internet | Tavily Search | Web search ... |
                    tavily_search | Search the web for current information ...",
    "entity_type": "tool",
    "enabled":     true
  }

get_server_capabilities result:
  {
    "server_name": "tavily-search",
    "server_id":   "6978e12b529328946c13297c",
    "path":        "/internet",
    "tools": [
      {"name": "tavily_search",   "description": "Search the web for current information..."},
      {"name": "tavily_extract",  "description": "Extract content from URLs..."},
      {"name": "tavily_crawl",    "description": "Crawl a website starting from a URL..."},
      {"name": "tavily_map",      "description": "Map a website structure..."},
      {"name": "tavily_research", "description": "Perform comprehensive research on a topic..."},
      {"name": "tavily_skill",    "description": "Search documentation for any library or API..."}
    ]
  }


═══════════════════════════════════════════════════════════════════
DECISION ALGORITHM
═══════════════════════════════════════════════════════════════════

Step 0 — Classify the request:
  • Single action, immediate result (search, create, update, delete, read)  → MCP path
  • Complex research, multi-step workflow, long-running analysis            → A2A path

─── MCP path ────────────────────────────────────────────────────

Step 1 — discover_servers first (default):
  discover_servers(query=<user_intent>, type_list=["tool"], top_n=3)

  For each result, read tool_name and content.
  If tool_name and its description in content directly match the user's intent:
    execute_tool(server_id=<result.server_id>, tool_name=<result.tool_name>, arguments={...})
    STOP.

  IF results are ambiguous or none match well → go to Step 2.
  IF user explicitly names a server (e.g. "use tavily", "用 tavily") → skip to Step 2.

Step 2 — get_server_capabilities (when target server is known):
  get_server_capabilities(server_name=<server_name>)
  Choose the best-matching tool from tools[].
  execute_tool(server_id=<caps.server_id>, tool_name=<chosen_tool.name>, arguments={...})
  STOP.

Step 3 — discover_domains (last resort):
  discover_domains(query=<user_intent>)
  Pick a relevant domain, then call get_server_capabilities on its server_names[0].
  If nothing matches → tell the user no capability is registered for this request.


═══════════════════════════════════════════════════════════════════
WORKED EXAMPLES
═══════════════════════════════════════════════════════════════════

─── Example 1: Web search ───────────────────────────────────────
User: "Search for the latest AI news"

→ discover_servers(query="search latest AI news", type_list=["tool"], top_n=3)
← [{"server_id": "6978e12b529328946c13297c", "server_name": "tavily-search",
    "tool_name": "tavily_search",
    "content": "tavily-search | /internet | ... | tavily_search | Search the web ..."}]

tool_name="tavily_search" matches "search" intent → execute directly:
→ execute_tool(server_id="6978e12b529328946c13297c",
               tool_name="tavily_search",
               arguments={"query": "latest AI news"})

─── Example 2: User names a server ──────────────────────────────
User: "用 tavily 帮我抓取这个页面的内容"

→ get_server_capabilities(server_name="tavily-search")
← tools: [tavily_search, tavily_extract, tavily_crawl, ...]

"抓取页面内容" matches tavily_extract:
→ execute_tool(server_id="6978e12b529328946c13297c",
               tool_name="tavily_extract",
               arguments={"urls": ["https://..."]})

─── Example 3: A2A agent ────────────────────────────────────────
User: "Find AWS case studies for fintech companies"

→ discover_agents(query="AWS case studies fintech", top_n=3)
← [{"agent_id": "69d9012356e96a32e89d474a", "agent_name": "AWS Research Agent",
    "content": "... Skill: AWS Research | Tags: aws, research, case-study"}]

→ invoke_agent(agent_id="69d9012356e96a32e89d474a",
               agent_url="https://agents.example.com/aws-research",
               message="Find AWS case studies for fintech companies",
               skill_id="aws_research")

─── Example 4: "What can you do?" ──────────────────────────────
→ discover_domains(query="")

═══════════════════════════════════════════════════════════════════
HARD RULES
═══════════════════════════════════════════════════════════════════

- NEVER invent tool_name, server_id, or agent_id — use values from discovery results only.
- NEVER call discover_servers and discover_agents for the same request — classify first.
- NEVER call discover_servers more than once per user request.
- server_id + tool_name MUST come from the same result object.
- agent_id MUST come from discover_agents or discover_domains.
- NEVER list all servers or agents unprompted.
"""


def create_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> FastMCP[McpAppContext]:
    """
    Factory function to create a stateless FastMCP application instance.

    Returns:
        Configured FastMCP application instance
    """

    @asynccontextmanager
    async def mcp_lifespan(server: FastMCP) -> AsyncIterator[McpAppContext]:
        """Manage MCP application lifecycle with type-safe context."""

        container = container_provider()
        if container is None:
            raise RuntimeError("Registry container is not initialized")

        async with AsyncClient(
            timeout=Timeout(30.0, read=60.0),
            follow_redirects=True,
            limits=Limits(max_connections=100, max_keepalive_connections=20),
        ) as proxy_client:
            yield McpAppContext(
                proxy_client=proxy_client,
                server_service=container.server_service,
                mcp_server_repo=container.mcp_server_repo,
                a2a_agent_repo=container.a2a_agent_repo,
                mcp_client_service=container.mcp_client_service,
                oauth_service=container.oauth_service,
                session_store=container.session_store,
            )

    # Configure transport security settings from environment variables
    transport_security_settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.mcpgw_enable_dns_rebinding_protection,
        allowed_hosts=[host.strip() for host in settings.mcpgw_allowed_hosts.split(",") if host.strip()],
        allowed_origins=[origin.strip() for origin in settings.mcpgw_allowed_origins.split(",") if origin.strip()],
    )

    mcp = FastMCP(
        "JarvisRegistry",
        lifespan=mcp_lifespan,
        event_store=InMemoryEventStore(max_events_per_stream=50, max_streams=500),
        instructions=_SYSTEM_INSTRUCTIONS,
        transport_security=transport_security_settings,
    )

    return mcp


def create_gateway_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> FastMCP[McpAppContext]:
    """Create the FastMCP app and register all prompts/tools in one place."""
    mcp = create_mcp_app(container_provider=container_provider)
    register_prompts(mcp)
    register_tools(mcp)
    return mcp


# ============================================================================
# MCP Prompts - Guide AI Assistant Behavior (Claude, ChatGPT, etc.)
# ============================================================================


def register_prompts(mcp: FastMCP) -> None:
    """
    Register prompts for the MCP application.

    Args:
        mcp: FastMCP application instance
    """

    @mcp.prompt()
    def gateway_capabilities():
        """📚 Overview of MCP Gateway capabilities and available services.

        Use this prompt to understand what services and tools are available through the gateway.
        This is automatically invoked when you need to know what you can do.
        """
        return _SYSTEM_INSTRUCTIONS


# ============================================================================
# Tool Registration
# ============================================================================


def register_tools(mcp: FastMCP) -> None:
    """
    Register all tools for the MCP application.

    Args:
        mcp: FastMCP application instance
    """
    # Register search tools (discover_tools, discover_servers)
    for tool_name, tool_func in search.get_tools():
        mcp.tool(name=tool_name)(tool_func)

    # Register registry API tools
    for tool_name, tool_func in proxied.get_tools():
        mcp.tool(name=tool_name)(tool_func)
