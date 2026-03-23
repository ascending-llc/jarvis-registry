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
- Discover tools, resources, prompts, or full server documents across registered MCP servers
- Execute downstream MCP tools through a unified proxy
- Access downstream resources and prompts through the same registry
- Route requests with the server's configured authentication and connection settings

GLOBAL WORKFLOW RULES:
1. If you do not already have a suitable tool for the user's request, call `discover_servers` first.
2. Do not respond that you lack capability until you have attempted discovery.
3. If a native fetch or direct access attempt fails with authentication, permission, or access errors, fall back to `discover_servers`.
4. Prefer `type_list=["tool"]` first. Use `type_list=["server"]` only when you need a full server document to inspect all capabilities on that server.

WHEN TO FALL BACK TO DISCOVERY:
- Private repository or API access fails
- Authentication or authorization fails (401, 403, permission denied)
- A specialized external service is likely needed
- The user asks what capabilities exist for a domain or service

TOKEN-EFFICIENT DISCOVERY:
- `type_list=["tool"]`: default and preferred for executable tools
- `type_list=["resource"]`: for data sources or URIs
- `type_list=["prompt"]`: for reusable prompt workflows
- `type_list=["server"]`: only when you need the full Mongo-style server document

CRITICAL RESULT INTERPRETATION RULE:
- Treat discovery results as full server documents only when `type_list` is exactly `["server"]`.
- In every other case, including `type_list=["tool"]`, treat each returned item as a directly usable result for execution purposes.

EXECUTION RULES:
- `execute_tool` always runs exactly one downstream MCP tool.
- The `tool_name` parameter of the `execute_tool` call must always be the final downstream MCP tool name.
- If the previous discovery call used exactly `type_list=["server"]`, first inspect the `$.config.toolFunctions` field of the server document, choose one tool entry, and pass that chosen entry's `mcpToolName` as `tool_name`. Only if `mcpToolName` is missing may you fall back to that tool entry's key or name.
- In every other discovery case, pass the returned `tool_name` unchanged into the `tool_name` parameter of the `execute_tool` call.
- Pair the chosen `tool_name` with the matching `server_id` from the same discovery result or chosen server document.

EXAMPLES:
- Weather or current events → `discover_servers(query="weather forecast", type_list=["tool"])`
- Web search → `discover_servers(query="web search news", type_list=["tool"])`
- Stock prices → `discover_servers(query="financial data stock market", type_list=["tool"])`
- Explore full capabilities of a server domain → `discover_servers(query="github", type_list=["server"])`
- Access failure on a protected service → `discover_servers(query="<service> authenticated", type_list=["tool"])`

SERVER-DOCUMENT EXAMPLE:
- If `discover_servers(..., type_list=["server"])` returns a server whose `$.config.toolFunctions` contains:
  - `add_numbers_mcp_minimal_mcp_iam -> mcpToolName="add_numbers"`
  - `greet_mcp_minimal_mcp_iam -> mcpToolName="greet"`
- Then first choose the single tool entry that matches the task.
- To execute the add tool, call `execute_tool(tool_name="add_numbers", server_id="<server id>", arguments={...})`.
- To execute the greet tool, call `execute_tool(tool_name="greet", server_id="<server id>", arguments={...})`.

TOOL-RESULT EXAMPLE:
- If discovery returns `{"tool_name": "tavily_search", "server_id": "abc123", ...}`, call `execute_tool(tool_name="tavily_search", server_id="abc123", arguments={...})`.
"""


def create_mcp_app(*, container_provider: Callable[[], "RegistryContainer | None"]) -> FastMCP:
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


def create_gateway_mcp_app(*, container_provider: Callable[[], "RegistryContainer | None"]) -> FastMCP:
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
