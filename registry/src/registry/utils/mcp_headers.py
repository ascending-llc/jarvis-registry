"""Registry entry point for building authenticated MCP request headers.

The shared, app-agnostic logic lives in ``registry_pkgs.oauth.headers`` (also used by
workflow-worker); this module supplies the registry's config/scopes so callers never wire
those up themselves. All registry header building runs interactively.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from redis import Redis

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.oauth.headers import HeaderBuildConfig
from registry_pkgs.oauth.headers import build_authenticated_headers as _build_authenticated_headers
from registry_pkgs.oauth.headers import build_complete_headers_for_server as _build_complete_headers_for_server
from registry_pkgs.oauth.types import StateMetadata
from registry_pkgs.types import UserContextDict

from ..auth.dependencies import effective_scopes_from_context
from ..core.config import settings

if TYPE_CHECKING:
    from registry_pkgs.oauth.oauth_service import MCPOAuthService


@lru_cache(maxsize=1)
def get_header_build_config() -> HeaderBuildConfig:
    """Snapshot the registry settings into a HeaderBuildConfig, built once and cached.

    Cached because the underlying settings are process-static after startup. Built lazily
    (not at import) so ``settings.encryption_key`` is read only after ``JarvisBaseSettings``
    has validated CREDS_KEY is non-empty — it is therefore always valid bytes here and is
    passed raw, matching the TokenService / MCPOAuthService wiring.
    """
    return HeaderBuildConfig(
        registry_app_name=settings.registry_app_name,
        redis_key_prefix=settings.redis_key_prefix,
        jwt_signing_config=settings.jwt_signing_config,
        encryption_key=settings.encryption_key,
    )


async def build_authenticated_headers(
    oauth_service: MCPOAuthService,
    server: ExtendedMCPServer,
    auth_context: UserContextDict,
    additional_headers: dict[str, str] | None = None,
    *,
    state_metadata: StateMetadata | None = None,
    redis_client: Redis | None = None,
) -> dict[str, str]:
    """Build full gateway + auth headers for an authenticated MCP request.

    Resolves the caller's effective scopes (group→scope mapping) and injects the registry
    HeaderBuildConfig, then delegates to the shared chain in interactive mode.
    """
    return await _build_authenticated_headers(
        oauth_service,
        server,
        auth_context,
        effective_scopes=effective_scopes_from_context(auth_context),
        cfg=get_header_build_config(),
        additional_headers=additional_headers,
        state_metadata=state_metadata,
        redis_client=redis_client,
    )


async def build_complete_headers_for_server(
    oauth_service: MCPOAuthService,
    server: ExtendedMCPServer,
    user_id: str | None = None,
    *,
    state_metadata: StateMetadata | None = None,
    redis_client: Redis | None = None,
) -> dict[str, str]:
    """Build the OAuth/apiKey/AgentCore auth headers for a server, keyed by ``user_id``.

    Injects the registry HeaderBuildConfig and delegates to the shared chain in interactive
    mode. Unlike :func:`build_authenticated_headers` this does not add the gateway base
    headers (X-User-Id / X-Scopes) and takes ``user_id`` rather than a full auth context.
    """
    return await _build_complete_headers_for_server(
        oauth_service,
        server,
        user_id,
        cfg=get_header_build_config(),
        state_metadata=state_metadata,
        redis_client=redis_client,
    )
