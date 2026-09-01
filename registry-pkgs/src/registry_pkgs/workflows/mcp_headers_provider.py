from __future__ import annotations

from collections.abc import Callable
from typing import Any

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.oauth.headers import HeaderBuildConfig, build_authenticated_headers
from registry_pkgs.oauth.oauth_service import MCPOAuthService
from registry_pkgs.types import UserContextDict

ScopeResolver = Callable[[UserContextDict], list[str]]


class McpHeadersProvider:
    """Build per-call MCP auth headers for manually-registered servers."""

    def __init__(
        self,
        *,
        oauth_service: MCPOAuthService,
        cfg: HeaderBuildConfig,
        scope_resolver: ScopeResolver,
        redis_client: Any | None = None,
        interactive: bool = True,
    ) -> None:
        self._oauth_service = oauth_service
        self._cfg = cfg
        self._scope_resolver = scope_resolver
        self._redis_client = redis_client
        self._interactive = interactive

    async def __call__(self, server: ExtendedMCPServer, auth_context: UserContextDict | None) -> dict[str, str]:
        if auth_context is None:
            raise ValueError("auth_context is required to build MCP headers")
        effective_scopes = self._scope_resolver(auth_context)
        return await build_authenticated_headers(
            self._oauth_service,
            server,
            auth_context,
            effective_scopes=effective_scopes,
            cfg=self._cfg,
            redis_client=self._redis_client,
            interactive=self._interactive,
        )


def make_mcp_headers_provider(
    *,
    oauth_service: MCPOAuthService,
    cfg: HeaderBuildConfig,
    scope_resolver: ScopeResolver,
    redis_client: Any | None = None,
    interactive: bool = True,
) -> McpHeadersProvider:
    """Factory used by the DI container (registry) and workflow-worker startup."""
    return McpHeadersProvider(
        oauth_service=oauth_service,
        cfg=cfg,
        scope_resolver=scope_resolver,
        redis_client=redis_client,
        interactive=interactive,
    )
