import base64
import logging
from dataclasses import dataclass
from typing import Any

from redis import Redis

from registry_pkgs.core.agentcore_jwt import parse_agentcore_runtime_access, sign_agentcore_jwt
from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.crypto_utils import decrypt_auth_fields
from registry_pkgs.core.exceptions import InternalServerException, UrlElicitationRequiredException
from registry_pkgs.core.header_utils import normalize_headers
from registry_pkgs.models import ExtendedMCPServer
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.types import UserContextDict

from .errors import AuthenticationError, MissingUserIdError, OAuthReAuthRequiredError, OAuthTokenError
from .oauth_service import MCPOAuthService
from .types import StateMetadata

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeaderBuildConfig:
    """Process-level settings the header-building chain needs, injected by the caller.

    Sourced from ``settings`` in each app (registry / workflow-worker): ``registry_app_name``,
    ``redis_key_prefix``, ``jwt_signing_config``, ``encryption_key``.
    """

    registry_app_name: str
    redis_key_prefix: str
    jwt_signing_config: JwtSigningConfig
    encryption_key: bytes | None


def _validate_and_merge_oauth_metadata(
    oauth_config: dict[str, Any] | None, oauth_metadata: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Merge OAuth metadata using database config.oauth as authoritative source.

    Database config.oauth (configured by admin) always takes priority over
    MCP server's .well-known metadata to prevent incorrect configurations.

    Args:
        oauth_config: OAuth configuration from registry database (config.oauth) - AUTHORITATIVE
        oauth_metadata: OAuth metadata from MCP server's /.well-known endpoint

    Returns:
        Merged OAuth metadata with database config.oauth overriding server metadata

    Example:
        Database config.oauth.authorization_servers: ["https://accounts.google.com"]
        Server metadata.authorization_servers: ["http://localhost:3080/"]  # WRONG
        Result: authorization_servers = ["https://accounts.google.com"] (from database config)
    """
    # If neither metadata nor config is provided, return empty dict
    if not oauth_metadata and not oauth_config:
        return {}

    # If no server metadata, return database config as-is
    if not oauth_metadata and oauth_config:
        return oauth_config.copy()

    # If no database config, use server metadata as-is
    if oauth_metadata and not oauth_config:
        return oauth_metadata.copy()

    # Both server metadata and database config exist:
    # start with server metadata, then override with database config fields
    merged_metadata: dict[str, Any] = oauth_metadata.copy()  # type: ignore[union-attr]
    merged_metadata.update(oauth_config)  # type: ignore[arg-type]

    return merged_metadata


async def build_complete_headers_for_server(
    oauth_service: MCPOAuthService,
    server: ExtendedMCPServer,
    user_id: str | None = None,
    *,
    cfg: HeaderBuildConfig,
    state_metadata: StateMetadata | None = None,
    redis_client: Redis | None = None,
    interactive: bool = True,
) -> dict[str, str]:
    """
    Build complete HTTP headers with ALL authentication types.
    Consolidates OAuth, apiKey, custom header, and AgentCore Runtime auth logic in one place.

    Args:
        oauth_service: OAuth service for OAuth token management
        server: Server document containing config
        user_id: User ID for OAuth token retrieval (required for OAuth servers)
        cfg: Process-level header-building settings
        state_metadata: OAuth flow state metadata
        redis_client: Redis client for JWT token caching
        interactive: When False, an unrefreshable OAuth token raises
            OAuthReAuthRequiredError directly (no interactive flow initiated)

    Returns:
        Complete headers dictionary ready for HTTP requests

    Raises:
        MissingUserIdError: If OAuth server requires user_id but none provided
        OAuthReAuthRequiredError: If OAuth re-authentication is needed
        OAuthTokenError: If OAuth token retrieval/refresh fails
        AuthenticationError: For other authentication failures
    """

    config = server.config or {}
    decrypted_config = decrypt_auth_fields(config, encryption_key=cfg.encryption_key)

    # Start with base MCP headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": cfg.registry_app_name,
    }

    # 1. Add custom headers FIRST (lowest priority)
    custom_headers = normalize_headers(decrypted_config.get("headers"))
    logger.debug("Custom header keys for %s: %s", server.serverName, list(custom_headers))
    if custom_headers:
        headers.update(custom_headers)

    # 2. Check AgentCore Runtime authentication (for federated AgentCore MCP servers)
    runtime_access_config = decrypted_config.get("runtimeAccess")

    if runtime_access_config:
        try:
            access_config = parse_agentcore_runtime_access(runtime_access_config)

            # Only handle JWT mode for now (IAM support can be added later if needed)
            if access_config.mode == AgentCoreRuntimeAccessMode.JWT:
                logger.info(f"Building JWT token for AgentCore Runtime server {server.serverName}")

                cache_key = f"{cfg.redis_key_prefix}:agentcore_jwt:{server.id}"
                token = sign_agentcore_jwt(
                    access_config.jwt,
                    subject=cfg.registry_app_name,
                    signing=cfg.jwt_signing_config,
                    cache_key=cache_key,
                    redis_client=redis_client,
                )
                headers["Authorization"] = f"Bearer {token}"
                logger.info(f"Added AgentCore Runtime JWT for {server.serverName}")
                return headers

            elif access_config.mode == AgentCoreRuntimeAccessMode.IAM:
                raise NotImplementedError(
                    f"IAM authentication not yet supported for AgentCore Runtime server {server.serverName}"
                )
            else:
                logger.warning(f"Unknown runtime access mode '{access_config.mode}' for server {server.serverName}")
        except NotImplementedError:
            raise
        except Exception as exc:
            logger.exception(f"Failed to build AgentCore Runtime authentication for {server.serverName}")
            raise AuthenticationError(f"Failed to authenticate with AgentCore Runtime: {exc}")
    elif decrypted_config.get("authProvider") == "bedrock-agentcore":
        # Server has authProvider but no runtimeAccess configuration
        logger.warning(
            f"Server {server.serverName} has authProvider='bedrock-agentcore' "
            f"but missing runtimeAccess configuration. Skipping runtime authentication."
        )

    # 3. Check OAuth and add OAuth headers (high priority, overrides custom headers)
    requires_oauth = decrypted_config.get("requiresOAuth", False) or "oauth" in decrypted_config

    if requires_oauth:
        if not user_id:
            raise MissingUserIdError(
                f"User ID required for OAuth server {server.serverName}",
                server_name=server.serverName,
            )

        logger.info(f"Building OAuth headers for {server.serverName}")

        # Validate and merge OAuth metadata with config.oauth as source of truth
        # This ensures correct authorization_servers are used for token validation
        oauth_config = decrypted_config.get("oauth")
        raw_oauth_metadata = decrypted_config.get("oauthMetadata", {})

        oauth_metadata = _validate_and_merge_oauth_metadata(
            oauth_config=oauth_config, oauth_metadata=raw_oauth_metadata
        )

        # Update server's oauthMetadata in-memory for this request
        # This ensures OAuth service uses correct authorization_servers
        if oauth_metadata:
            config["oauthMetadata"] = oauth_metadata
            server.config = config
            logger.debug(
                f"Validated OAuth metadata for token retrieval: "
                f"authorization_servers={oauth_metadata.get('authorization_servers')}"
            )

        # Get OAuth token (handles refresh automatically)
        access_token, auth_url, error = await oauth_service.get_valid_access_token(
            user_id=user_id, server=server, state_metadata=state_metadata, interactive=interactive
        )

        if auth_url:
            raise OAuthReAuthRequiredError(
                f"OAuth re-authentication required for {server.serverName}",
                auth_url=auth_url,
                server_name=server.serverName,
            )

        if error:
            raise OAuthTokenError(
                f"OAuth token error for {server.serverName}: {error}",
                server_name=server.serverName,
            )

        if not access_token:
            raise OAuthTokenError(
                f"No valid OAuth token available for {server.serverName}",
                server_name=server.serverName,
            )

        # Override any existing Authorization header with OAuth Bearer token
        # This ensures OAuth always takes priority over custom headers
        headers["Authorization"] = f"Bearer {access_token}"
        logger.debug(f"OAuth Bearer token added for {server.serverName} (overrides any custom Authorization header)")
        return headers

    # 4. Handle apiKey authentication (if not OAuth or AgentCore Runtime)
    api_key_config = decrypted_config.get("apiKey")
    if api_key_config and isinstance(api_key_config, dict):
        key_value = api_key_config.get("key")
        authorization_type = api_key_config.get("authorization_type", "bearer").lower()

        if key_value:
            if authorization_type == "bearer":
                headers["Authorization"] = f"Bearer {key_value}"
                logger.debug(f"Added Bearer apiKey for {server.serverName}")
            elif authorization_type == "basic":
                # Handle base64 encoding
                try:
                    base64.b64decode(key_value, validate=True)
                    # Already base64 encoded
                    headers["Authorization"] = f"Basic {key_value}"
                    logger.debug(f"Added Basic auth (pre-encoded) for {server.serverName}")
                except Exception:
                    # Not base64 encoded, encode it
                    encoded_key = base64.b64encode(key_value.encode()).decode()
                    headers["Authorization"] = f"Basic {encoded_key}"
                    logger.debug(f"Added Basic auth (auto-encoded) for {server.serverName}")
            elif authorization_type == "custom":
                custom_header = api_key_config.get("custom_header")
                if custom_header:
                    headers[custom_header] = key_value
                    logger.debug(f"Added custom auth header '{custom_header}' for {server.serverName}")
                else:
                    logger.warning(
                        f"apiKey with authorization_type='custom' but no custom_header for {server.serverName}"
                    )
            else:
                logger.warning(
                    f"Unknown authorization_type: {authorization_type}, defaulting to Bearer for {server.serverName}"
                )
                headers["Authorization"] = f"Bearer {key_value}"

    return headers


async def build_authenticated_headers(
    oauth_service: MCPOAuthService,
    server: ExtendedMCPServer,
    auth_context: UserContextDict,
    *,
    effective_scopes: list[str],
    cfg: HeaderBuildConfig,
    additional_headers: dict[str, str] | None = None,
    state_metadata: StateMetadata | None = None,
    redis_client: Redis | None = None,
    interactive: bool = True,
) -> dict[str, str]:
    """
    Build complete headers with authentication for MCP server requests.
    Consolidates auth logic used by all proxy endpoints.

    Supports multiple authentication types:
    - AgentCore Runtime: JWT authentication for federated AgentCore MCP servers (with caching)
    - OAuth: External access token (RFC 6750) for MCP server resource access
    - Internal JWT: Gateway-to-MCP authentication (always included)
    - API Key: Bearer/Basic/Custom API key authentication

    Args:
        oauth_service: OAuth service for OAuth token management
        server: MCP server document
        auth_context: Gateway authentication context (user, client_id, scopes)
        effective_scopes: Resolved scopes for the X-Scopes header (caller-computed;
            registry maps groups→scopes, workflow-worker's scheduled runs carry none)
        cfg: Process-level header-building settings
        additional_headers: Optional additional headers to merge
        state_metadata: OAuth flow state metadata
        redis_client: Redis client for JWT token caching
        interactive: When False, an unrefreshable OAuth token raises
            OAuthReAuthRequiredError instead of converting to UrlElicitationRequiredException

    Returns:
        Complete headers dict with authentication

    Raises:
        UrlElicitationRequiredException: If an interactive caller must perform out-of-band re-auth.
        OAuthReAuthRequiredError: If a non-interactive caller hits unrefreshable OAuth re-auth.
        InternalServerException: If UserContextDict.user_id is None, or on unexpected token errors.
    """
    # Validate user_id is present (auth-server always includes it in JWT)
    if auth_context["user_id"] is None:
        logger.error(f"Missing user_id in auth_context. Available keys: {list(auth_context.keys())}")
        raise InternalServerException("Invalid authentication context: missing user_id")

    # Build base headers (filter out empty values to avoid httpx errors)
    headers: dict[str, str] = {
        "X-User-Id": auth_context.get("user_id") or "",
        "X-Username": auth_context.get("username") or "",
        "X-Scopes": " ".join(effective_scopes),
    }
    # Remove empty header values (httpx requires non-empty strings)
    headers = {k: v for k, v in headers.items() if v}

    # Merge additional headers if provided
    if additional_headers:
        headers.update(additional_headers)

    # Build complete authentication headers (OAuth, apiKey, custom, AgentCore Runtime)
    try:
        user_id = auth_context["user_id"]  # Already validated above
        auth_headers = await build_complete_headers_for_server(
            oauth_service,
            server,
            user_id,
            cfg=cfg,
            state_metadata=state_metadata,
            redis_client=redis_client,
            interactive=interactive,
        )

        # Merge auth headers with case-insensitive override logic
        # Protected headers that won't be overridden by auth headers
        protected_headers = {"x-user-id", "x-username", "x-client-id", "x-scopes", "accept"}

        # Build a case-insensitive map of existing header names to their original keys
        lowercase_header_map = {k.lower(): k for k in headers}

        for auth_key, auth_value in auth_headers.items():
            auth_key_lower = auth_key.lower()
            if auth_key_lower in protected_headers:
                continue

            # Remove any existing header with same name (case-insensitive)
            existing_key = lowercase_header_map.get(auth_key_lower)
            if existing_key is not None:
                headers.pop(existing_key, None)

            # Add/override with the auth header and update the lowercase map
            headers[auth_key] = auth_value
            lowercase_header_map[auth_key_lower] = auth_key

        logger.debug(f"Built complete authentication headers for {server.serverName}")
        return headers

    except OAuthReAuthRequiredError as exc:
        if not interactive:
            raise
        logger.debug(f"in-session re-auth required for server {exc.server_name}")

        raise UrlElicitationRequiredException(
            "OAuth re-authentication required", auth_url=exc.auth_url, server_name=exc.server_name
        )
    except (OAuthTokenError, AuthenticationError):
        logger.exception("unexpected OAuth token exception")

        raise InternalServerException("internal server error when building OAuth token on behalf of user")
