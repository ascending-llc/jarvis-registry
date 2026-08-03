from __future__ import annotations

from typing import TYPE_CHECKING, Any

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

from ..auth.dependencies import UserContextDict
from ..mcpgw.tools.utils import build_authenticated_headers

if TYPE_CHECKING:
    from ..services.oauth.oauth_service import MCPOAuthService


class McpHeadersProvider:
    """Build per-call MCP auth headers for manually-registered servers.

    Use as the ``mcp_headers_provider`` callback for ``make_mcp_executor`` /
    ``build_executor_registry``.  This lives in the ``registry`` app (not
    ``registry-pkgs``) because it depends on the concrete ``MCPOAuthService``
    and ``build_authenticated_headers``.
    """

    def __init__(
        self,
        *,
        oauth_service: MCPOAuthService,
        redis_client: Any | None = None,
    ) -> None:
        self._oauth_service = oauth_service
        self._redis_client = redis_client

    async def __call__(self, server: ExtendedMCPServer, auth_context: UserContextDict | None) -> dict[str, str]:
        if auth_context is None:
            raise ValueError("auth_context is required to build MCP headers")
        return await build_authenticated_headers(
            self._oauth_service,
            server,
            auth_context,
            redis_client=self._redis_client,
        )


def make_mcp_headers_provider(
    *,
    oauth_service: MCPOAuthService,
    redis_client: Any | None = None,
) -> McpHeadersProvider:
    """Factory used by the DI container."""
    return McpHeadersProvider(
        oauth_service=oauth_service,
        redis_client=redis_client,
    )
