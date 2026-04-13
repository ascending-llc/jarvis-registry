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

KEY CAPABILITIES:
- Discover tools, resources, and prompts across registered MCP servers
- Execute downstream MCP tools through a unified proxy
- Access downstream resources and prompts through the same registry
- Route requests with the server's configured authentication and connection settings

GLOBAL WORKFLOW RULES:
1. If you do not already have a suitable tool for the user's request, call `discover_servers` first.
2. Do not respond that you lack capability until you have attempted discovery.
3. If a native fetch or direct access attempt fails with authentication, permission, or access errors, fall back to `discover_servers`.
4. Prefer `type_list=["tool"]` first.

WHEN TO FALL BACK TO DISCOVERY:
- Private repository or API access fails
- Authentication or authorization fails (401, 403, permission denied)
- A specialized external service is likely needed
- The user asks what capabilities exist for a domain or service

DISCOVERY TYPES:
- `type_list=["tool"]`: default and preferred — returns executable tools
- `type_list=["resource"]`: for data sources, cached results, or URI-addressable content
- `type_list=["prompt"]`: for reusable prompt workflows

CORE RULE — NO SECONDARY LOOKUP NEEDED:
Every discovery result already contains all fields required for execution.
Do not inspect nested config, do not look up additional documents, do not transform any field.

EXECUTION PATTERN (by type):
- Tool    → `execute_tool(tool_name=<result.tool_name>, server_id=<result.server_id>, arguments={...})`
- Resource → `read_resource(server_id=<result.server_id>, resource_uri=<result.resource_uri>)`
- Prompt  → `execute_prompt(server_id=<result.server_id>, prompt_name=<result.prompt_name>, arguments={...})`

EXECUTION CONSTRAINTS:
- `execute_tool` runs exactly one downstream MCP tool per call.
- Pass `tool_name` exactly as returned — do not invent, rename, or rewrite it.
- Always pair `tool_name` with the `server_id` from the same discovery result.

COMPLETE WORKFLOW EXAMPLE:
  Step 1 — Discover:
    discover_servers(query="web search news", type_list=["tool"])
    → [{"tool_name": "tavily_search", "server_id": "abc123", "server_name": "tavily", ...}]

  Step 2 — Execute immediately using the returned fields:
    execute_tool(tool_name="tavily_search", server_id="abc123", arguments={"query": "AI news"})

DISCOVERY EXAMPLES:
- Weather or current events → `discover_servers(query="weather forecast", type_list=["tool"])`
- Web search              → `discover_servers(query="web search news", type_list=["tool"])`
- Stock prices            → `discover_servers(query="financial data stock market", type_list=["tool"])`
- GitHub operations       → `discover_servers(query="github repositories", type_list=["tool"])`
- Cached / URI data       → `discover_servers(query="cached results", type_list=["resource"])`
- Prompt templates        → `discover_servers(query="code review template", type_list=["prompt"])`
- Auth failure fallback   → `discover_servers(query="<service> authenticated", type_list=["tool"])`
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
