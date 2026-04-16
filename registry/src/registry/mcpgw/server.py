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

_SYSTEM_INSTRUCTIONS = """This MCP Gateway provides unified discovery and execution for registered MCP entities.

KEY CAPABILITIES:
- Discover tools, resources, and prompts through a unified vector index
- Execute downstream MCP tools through a unified proxy
- Read downstream MCP resources
- Execute downstream MCP prompts

IMPORTANT MENTAL MODEL:
- Discovery is ENTITY-based, not SERVER-based.
- The discovery tool returns matched entity documents from the vector index.
- Results are already execution-ready.
- Do not perform secondary lookups or name translation after discovery.

GLOBAL WORKFLOW RULES:
1. If you do not already have a suitable tool, call `discover_entities` first.
2. Prefer `type_list=["tool"]` first for action-oriented tasks.
3. Do not claim lack of capability until discovery has been attempted.
4. If a direct/native attempt fails with authentication or permission errors, fall back to discovery.

DISCOVERY RESULT TYPES:
- `type_list=["tool"]`
  returns tool entity documents with `tool_name` + `server_id`
- `type_list=["resource"]`
  returns resource entity documents with `resource_uri` + `server_id`
- `type_list=["prompt"]`
  returns prompt entity documents with `prompt_name` + `server_id`

CORE RULE — RESULTS ARE EXECUTION-READY:
Every discovery result already contains the field needed for execution.
Do not inspect nested config.
Do not perform a second lookup.
Do not transform returned names.

EXECUTION PATTERN:
- Tool     -> `execute_tool(tool_name=<result.tool_name>, server_id=<result.server_id>, arguments={...})`
- Resource -> `read_resource(server_id=<result.server_id>, resource_uri=<result.resource_uri>)`
- Prompt   -> `execute_prompt(server_id=<result.server_id>, prompt_name=<result.prompt_name>, arguments={...})`

NAME HANDLING RULES:
- `tool_name` is the canonical downstream MCP tool name from the vector database.
- Never derive it from wrapper names, display labels, `mcpToolName`, `mcp_tool_name`, or casing variants.
- Use the returned `tool_name` verbatim.

EXAMPLE:
Step 1 — Discover:
  discover_entities(query="web search news", type_list=["tool"])
  -> [{"entity_type": "tool", "tool_name": "tavily_search", "server_id": "abc123", ...}]

Step 2 — Execute:
  execute_tool(tool_name="tavily_search", server_id="abc123", arguments={"query": "AI news"})
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
    # Register entity discovery tools
    for tool_name, tool_func in search.get_tools():
        mcp.tool(name=tool_name)(tool_func)

    # Register registry API tools
    for tool_name, tool_func in proxied.get_tools():
        mcp.tool(name=tool_name)(tool_func)
