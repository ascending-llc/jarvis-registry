from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from ..core.config import settings
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
Errors, denied access and unavailable services are not empty results or proof of no capability. When an error says the user must consent or re-authorize in a browser, show the user its URL verbatim and wait for them to finish before retrying.
If no match, refine or survey; check both discovery types when unclear. Avoid repeated identical searches; report no match after these fallbacks. Rediscover stale IDs. Limit execution retries to 2 per request, then report the outcome."""


def create_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> MCPServer[McpAppContext]:
    """
    Factory function to create a stateless MCPServer application instance.

    Returns:
        Configured MCPServer application instance
    """

    # Stateless MCPServer enters the lifespan on every request, so it must not open or close per-request
    # resources: the proxy client lives on the container, and the assembled context is cached here and
    # yielded unchanged on every entry.
    cached_context: McpAppContext | None = None

    @asynccontextmanager
    async def mcp_lifespan(server: MCPServer) -> AsyncIterator[McpAppContext]:
        """Assemble the MCP app context once from container references and yield it on every entry."""
        nonlocal cached_context
        if cached_context is None:
            container = container_provider()
            if container is None:
                raise RuntimeError("Registry container is not initialized")
            cached_context = McpAppContext(
                proxy_client=container.mcpgw_proxy_client,
                a2a_client_registry=container.a2a_client_registry,
                server_service=container.server_service,
                mcp_server_repo=container.mcp_server_repo,
                a2a_agent_repo=container.a2a_agent_repo,
                search_service=container.search_service,
                mcp_client_service=container.mcp_client_service,
                oauth_service=container.oauth_service,
                client_info_store=container.client_info_store,
                redis_client=container.redis_client,
                jwt_signing_config=settings.jwt_signing_config,
                acl_service=container.acl_service,
                consent_store=container.consent_store,
                pending_consent_store=container.pending_consent_store,
            )
        yield cached_context

    # mcp 2.x: stateless_http / transport_security are no longer MCPServer constructor
    # args — they move to streamable_http_app() at the ASGI mount site (see app_factory
    # and build_transport_security_settings()).
    mcp = MCPServer(
        "JarvisRegistry",
        lifespan=mcp_lifespan,
        instructions=_SYSTEM_INSTRUCTIONS,
    )

    return mcp


def build_transport_security_settings() -> TransportSecuritySettings:
    """Transport-security settings from env, for streamable_http_app() (mcp 2.x).

    In mcp 1.x these were a MCPServer constructor arg; in 2.x the ASGI mount site
    owns them, so the mount (app_factory) builds and passes them.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.mcpgw_enable_dns_rebinding_protection,
        allowed_hosts=[host.strip() for host in settings.mcpgw_allowed_hosts.split(",") if host.strip()],
        allowed_origins=[origin.strip() for origin in settings.mcpgw_allowed_origins.split(",") if origin.strip()],
    )


def create_gateway_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> MCPServer[McpAppContext]:
    """Create the MCPServer app and register its tools."""
    mcp = create_mcp_app(container_provider=container_provider)
    register_tools(mcp)
    return mcp


# ============================================================================
# Tool Registration
# ============================================================================


def register_tools(mcp: MCPServer) -> None:
    """
    Register all tools for the MCP application.

    Args:
        mcp: MCPServer application instance
    """
    for module in (search, agent, server):
        for tool_name, tool_func in module.get_tools():
            mcp.tool(name=tool_name, description=inspect.cleandoc(tool_func.__doc__ or ""))(tool_func)
