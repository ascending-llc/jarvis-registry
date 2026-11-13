from __future__ import annotations

import functools
import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server._otel import OpenTelemetryMiddleware
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError, UrlElicitationRequiredError
from mcp.types import CallToolResult, TextContent

from registry_pkgs.core.exceptions import McpGatewayException

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


def build_transport_security_settings() -> TransportSecuritySettings:
    """Build DNS-rebinding / allowed-host transport security from env settings.

    In v2, transport security moves off the server constructor and onto ``streamable_http_app(...)``,
    so ``app_factory`` builds the ASGI app and needs this at mount time.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.mcpgw_enable_dns_rebinding_protection,
        allowed_hosts=[host.strip() for host in settings.mcpgw_allowed_hosts.split(",") if host.strip()],
        allowed_origins=[origin.strip() for origin in settings.mcpgw_allowed_origins.split(",") if origin.strip()],
    )


def create_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> MCPServer[McpAppContext]:
    """
    Factory function to create a stateless MCPServer application instance.

    Returns:
        Configured MCPServer application instance
    """

    # In v2 the server lifespan runs once per session manager (not per request). The proxy client lives
    # on the container, so the assembled context is cached here and yielded unchanged on every entry.
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

    # `instructions` MUST be keyword: v2's positional order is (name, title, description, instructions),
    # so a positional string would land in `title`. Transport settings (stateless_http, json_response,
    # transport_security) move to streamable_http_app(...) and are applied in app_factory.
    mcp = MCPServer(
        "JarvisRegistry",
        instructions=_SYSTEM_INSTRUCTIONS,
        lifespan=mcp_lifespan,
        # The default InMemorySubscriptionBus is per-process and the gateway's tool list is static.
        subscriptions=False,
    )

    # Drop the SDK's built-in OpenTelemetryMiddleware. It parents a SERVER span on the client-supplied
    # `_meta.traceparent`, which violates our rule of never trusting client trace context on external
    # paths, and it duplicates the spans mcpgw/tracing.py already emits. Keep the rest (RequestStateBoundary).
    mcp.middleware[:] = [m for m in mcp.middleware if not isinstance(m, OpenTelemetryMiddleware)]

    return mcp


def create_gateway_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> MCPServer[McpAppContext]:
    """Create the MCPServer app and register its tools."""
    mcp = create_mcp_app(container_provider=container_provider)
    register_tools(mcp)
    return mcp


# ============================================================================
# Tool Registration
# ============================================================================


def _wrap_tool_error_shape(name: str, tool_func: Callable) -> Callable:
    """Preserve the v1 wire shape for the downstream-error path on top of v2's tool error handling.

    v1 turned any tool exception into ``CallToolResult(isError=True, text="Error executing tool ...")``,
    except ``UrlElicitationRequiredError`` (-32042). v2 instead makes ``MCPError`` a top-level JSON-RPC
    error and collapses every other exception into ``UnexpectedToolError``, dropping the message.

    This wrapper restores the v1 shape for ``MCPError`` and ``McpGatewayException`` (the downstream-error
    and InternalServerException paths), re-raises ``UrlElicitationRequiredError`` so the SDK still emits
    -32042, and leaves any other exception to the SDK. ``functools.wraps`` keeps the original signature so
    the SDK still sees the ``Context`` parameter for dependency injection.
    """

    @functools.wraps(tool_func)
    async def wrapper(*args, **kwargs):
        try:
            return await tool_func(*args, **kwargs)
        except UrlElicitationRequiredError:
            raise
        except (MCPError, McpGatewayException) as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error executing tool {name}: {exc}")],
                is_error=True,
            )

    return wrapper


def register_tools(mcp: MCPServer) -> None:
    """
    Register all tools for the MCP application.

    Args:
        mcp: MCPServer application instance
    """
    for module in (search, agent, server):
        for tool_name, tool_func in module.get_tools():
            wrapped = _wrap_tool_error_shape(tool_name, tool_func)
            mcp.tool(name=tool_name, description=inspect.cleandoc(tool_func.__doc__ or ""))(wrapped)
