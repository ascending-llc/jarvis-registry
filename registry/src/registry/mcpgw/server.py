from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import AsyncClient, Limits, Timeout
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..core.config import settings
from .core.event_store import InMemoryEventStore
from .core.types import McpAppContext
from .tools import proxied, search

_SYSTEM_INSTRUCTIONS = """This MCP Gateway provides unified access to 100+ MCP servers, tools, resources, and prompts through a centralized discovery and execution interface.

KEY CAPABILITIES:
- 🔍 Discover any tool, resource, or prompt across all registered MCP servers
- 🚀 Execute tools from any MCP server through unified proxy
- 📚 Access resources (URIs, caches, data sources) from any server
- 💬 Use prompts from any server for specialized workflows
- 🎯 Automatic routing, authentication, and execution with proper credentials

CRITICAL RULE - ALWAYS USE DISCOVERY FIRST:
⚠️ MANDATORY: If you don't have a suitable tool for the user's request, you MUST use discover_servers to find one.
⚠️ NEVER respond with "I don't have the capability" without first attempting discovery.
⚠️ The gateway provides access to 100+ specialized tools - they must be discovered before use.

CRITICAL RULE - WHEN NATIVE TOOLS FAIL:
⚠️ If you encounter FAILURES (fetch fails, can't access resource, authentication errors, permission denied):
  1. DON'T give up or say "I can't access this" - The registry has specialized authenticated tools
  2. IMMEDIATELY use discover_servers to find a specialized tool for that service
  3. Specialized tools in registry have proper authentication/authorization configured
  4. Most services (GitHub, APIs, databases) have authenticated tools available

EXAMPLES OF WHEN TO FALL BACK TO REGISTRY:
✓ Fetch fails on private repository URL → discover_servers(query="<service> repository", type_list=["tool"])
✓ Can't access API endpoint → discover_servers(query="<service name> API", type_list=["tool"])
✓ Authentication error (401/403) → discover_servers(query="<service> authenticated", type_list=["tool"])
✓ Rate limit exceeded → discover_servers(query="<service> premium access", type_list=["tool"])
✓ Permission denied → discover_servers(query="<service> <operation>", type_list=["tool"])
✓ Resource unavailable → Specialized tools in registry have proper credentials and access

WHEN TO USE DISCOVERY TOOLS:
✓ User needs external/real-time data (news, weather, stock prices, current events)
✓ User asks "what can you do?" → Use discover_servers with type_list=["server"]
✓ User mentions specific services (GitHub, databases, APIs, search engines)
✓ User requests functionality you don't have built-in → ALWAYS try discover_servers first
✓ You encounter a task requiring specialized tools → discover_servers before saying "I can't"
✓ User asks about capabilities in a domain → Use discover_servers with domain keyword
✓ Native tool FAILED → Try discover_servers to find authenticated/specialized alternative

DISCOVERY WORKFLOW:
1. User makes request → Check if you have suitable tools
2. No suitable tools? → MUST call discover_servers("description of need", type_list=["tool"])
3. Review discovered tools/servers
4. Execute appropriate tool with execute_tool(server_path, tool_name, arguments)
5. Present results to user

TOKEN-EFFICIENT DISCOVERY:
- Use type_list=["tool"] (default) for specific executable tools (returns 3 results)
- Use type_list=["resource"] for data sources/URIs (returns 3 results)
- Use type_list=["prompt"] for workflows (returns 3 results)
- Use type_list=["server"] only when exploring all capabilities (returns 1 server, token-heavy)

EXAMPLES:
- "What's the weather?" → discover_servers(query="weather forecast", type_list=["tool"])
- "Search for news on AI" → discover_servers(query="web search news", type_list=["tool"])
- "What can you do?" → discover_servers(query="", type_list=["server"])
- "Get stock prices" → discover_servers(query="financial data stock market", type_list=["tool"])
- "Send an email" → discover_servers(query="email send message", type_list=["tool"])
- Fetch/access fails → discover_servers(query="<service> <operation>", type_list=["tool"])

RESILIENCE PATTERN:
```
# When you see errors (fetch failed, 403, 401, permission denied):
if error_occurred:
    # Don't give up! Find authenticated tool from registry
    tools = discover_servers(query="<service> <operation>", type_list=["tool"])
    if tools:
        result = execute_tool(tools[0]["server_path"], tools[0]["tool_name"], args)

# For authenticated resources, prefer registry tools proactively:
# - Private repositories → discover_servers("<service> repository", type_list=["tool"])
# - Private APIs → discover_servers("<api name>", type_list=["tool"])
# - Database queries → discover_servers("database <operation>", type_list=["tool"])
# - Authenticated services → discover_servers("<service> authenticated", type_list=["tool"])
```

ALWAYS be proactive: Discover and use available tools automatically. When native tools fail, try registry tools - they often have proper authentication and elevated permissions!
"""


@asynccontextmanager
async def mcp_lifespan(server: FastMCP) -> AsyncIterator[McpAppContext]:
    """Manage MCP application lifecycle with type-safe context."""

    async with AsyncClient(
        timeout=Timeout(30.0, read=60.0),
        follow_redirects=True,
        limits=Limits(max_connections=100, max_keepalive_connections=20),
    ) as proxy_client:
        yield McpAppContext(proxy_client=proxy_client)


def create_mcp_app() -> FastMCP:
    """
    Factory function to create a stateless FastMCP application instance.

    Returns:
        Configured FastMCP application instance
    """
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
