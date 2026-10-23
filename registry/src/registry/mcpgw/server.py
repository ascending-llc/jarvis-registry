from __future__ import annotations

import warnings
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from httpx import AsyncClient, Limits, Timeout
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning

from ..core.config import settings
from .core.event_store import InMemoryEventStore
from .core.types import McpAppContext
from .tools import agent, search, server

if TYPE_CHECKING:
    from ..container import RegistryContainer

# Keep critical guidance on the first line for clients that summarize or truncate instructions.
# Detailed workflows and examples live in docs/features/registry-mcp-discovery.md.
_SYSTEM_INSTRUCTIONS = """Jarvis Registry provides MCP tools and A2A agents. Before claiming a needed external capability is unavailable, try Registry discovery when permitted.
Use discover_servers for tools/resources/prompts and discover_agents for delegation; start with concise keywords, top_n=3.
Ordinary text-only work needs no discovery. Respect user restrictions and required approvals.
Match descriptions, not scores. Use discovered IDs/names verbatim and follow schemas; never invent identifiers, schemas or missing inputs.
Errors, denied access and unavailable services are not empty results or proof of no capability. Surface auth errors and wait for reauthorization.
If no match, refine or survey; check both discovery types when unclear. Avoid repeated identical searches; report no match after these fallbacks. Rediscover stale IDs. Limit execution retries to 2 per request, then report the outcome."""


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
                a2a_client_registry=container.a2a_client_registry,
                server_service=container.server_service,
                mcp_server_repo=container.mcp_server_repo,
                a2a_agent_repo=container.a2a_agent_repo,
                search_service=container.search_service,
                mcp_client_service=container.mcp_client_service,
                oauth_service=container.oauth_service,
                session_store=container.session_store,
                redis_client=container.redis_client,
                jwt_signing_config=settings.jwt_signing_config,
                acl_service=container.acl_service,
                consent_store=container.consent_store,
                pending_consent_store=container.pending_consent_store,
            )

    # Configure transport security settings from environment variables
    transport_security_settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.mcpgw_enable_dns_rebinding_protection,
        allowed_hosts=[host.strip() for host in settings.mcpgw_allowed_hosts.split(",") if host.strip()],
        allowed_origins=[origin.strip() for origin in settings.mcpgw_allowed_origins.split(",") if origin.strip()],
    )

    # mcp's FastMCP Settings (a pydantic-settings BaseSettings) has a `lifespan` field whose
    # forward reference to `FastMCP` itself is never resolved via model_rebuild() — an upstream
    # bug unfixed in the 1.x line as of mcp 1.29.0, only removed in the breaking v2 rewrite.
    # Suppressed locally (not via a global filter) because some of our other dependencies
    # (e.g. weaviate) call warnings.simplefilter("default") on import, which resets the global
    # filter list and would silently defeat a suppression registered earlier at import time.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Field 'lifespan' has an incomplete definition",
            category=IncompleteFieldDefinitionWarning,
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
        Clients may retrieve this prompt explicitly; registration does not load it automatically.
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

    # Register A2A agent invocation tool
    for tool_name, tool_func in agent.get_tools():
        mcp.tool(name=tool_name)(tool_func)

    # Register registry API tools
    for tool_name, tool_func in server.get_tools():
        mcp.tool(name=tool_name)(tool_func)
