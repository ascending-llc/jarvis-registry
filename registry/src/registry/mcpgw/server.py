from __future__ import annotations

import inspect
import warnings
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning

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


def create_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> FastMCP[McpAppContext]:
    """
    Factory function to create a stateless FastMCP application instance.

    Returns:
        Configured FastMCP application instance
    """

    # Stateless FastMCP enters the lifespan on every request, so it must not open or close per-request
    # resources: the proxy client lives on the container, and the assembled context is cached here and
    # yielded unchanged on every entry.
    cached_context: McpAppContext | None = None

    @asynccontextmanager
    async def mcp_lifespan(server: FastMCP) -> AsyncIterator[McpAppContext]:
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
            # Stateless so any pod can serve any request (no process-local session affinity). Leave
            # json_response at its False default: downstream progress/log/list-changed notifications are
            # forwarded on the POST's own SSE stream via related_request_id, which the JSON path drops.
            stateless_http=True,
            instructions=_SYSTEM_INSTRUCTIONS,
            transport_security=transport_security_settings,
        )

    return mcp


def create_gateway_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> FastMCP[McpAppContext]:
    """Create the FastMCP app and register its tools."""
    mcp = create_mcp_app(container_provider=container_provider)
    register_tools(mcp)
    return mcp


# ============================================================================
# Tool Registration
# ============================================================================


def register_tools(mcp: FastMCP) -> None:
    """
    Register all tools for the MCP application.

    Args:
        mcp: FastMCP application instance
    """
    for module in (search, agent, server):
        for tool_name, tool_func in module.get_tools():
            mcp.tool(name=tool_name, description=inspect.cleandoc(tool_func.__doc__ or ""))(tool_func)
