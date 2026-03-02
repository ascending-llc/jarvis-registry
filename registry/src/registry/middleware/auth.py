import base64
import logging
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from jwt import ExpiredSignatureError, InvalidTokenError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import compile_path

from registry_pkgs.core.scopes import load_scopes_config, map_groups_to_scopes
from auth_utils.models import UserContextDict
from auth_utils.jwt_utils import decode_jwt, get_token_kid

from ..auth.dependencies import signer
from ..core.config import settings
from ..core.telemetry_decorators import AuthMetricsContext
from ..utils.crypto_utils import verify_access_token


logger = logging.getLogger(__name__)


class UnifiedAuthMiddleware(BaseHTTPMiddleware):
    """
    A unified authentication middleware that encapsulates the functionality of `enhanced_auth` and `nginx_proxied_auth`.

    It automatically attempts all authentication methods and stores the results in `request.state`.

    Path Matching Logic:
    --------------------
    1. public_paths_compiled: Paths that are PUBLICLY accessible (no authentication required)
       - These act as EXCEPTIONS to authenticated paths via double-check logic
       - Use specific patterns to carve out public endpoints from broader authenticated patterns
       - Example: "/api/{versions}/mcp/{server_name}/oauth/callback" is public despite matching broader MCP pattern

    How to Define Paths:
    --------------------
    public_paths_compiled:
      - Define SPECIFIC patterns that should be accessible without auth
      - These override authenticated patterns via double-check
      - Use more specific paths to carve out exceptions
      - Examples:
        * "/api/{versions}/mcp/{server_name}/oauth/callback" - Specific OAuth callback (public)
        * "/.well-known/{path:path}" - OAuth discovery endpoints (must be public per RFC)
        * "/health" - Health check endpoint (public)
    """

    def __init__(self, app):
        super().__init__(app)
        # Paths that require authentication (checked before public paths)
        # self.authenticated_paths_compiled = self._compile_patterns([
        #     "/api/auth/me",
        #     "/api/{versions}/servers/{path:path}",
        #     "/api/{versions}/servers",
        #     "/proxy/{path:path}",
        #     "/api/{versions}/mcp/{path:path}",
        #     "/api/search/{path:path}",
        # ])
        self.public_paths_compiled = self._compile_patterns(
            [
                "/",
                "/login",
                "/health",
                "/docs",
                "/openapi.json",
                "/static/{path:path}",
                "/redirect",
                "/redirect/{provider}",
                "/api/auth/providers",
                "/api/auth/config",
                f"/api/{settings.API_VERSION}/mcp/{{server_name}}/oauth/callback",  # OAuth callback is public
                "/.well-known/{path:path}",  # OAuth discovery endpoints must be public
            ]
        )

        # =====================================================================
        # INTERNAL PATHS (Admin/Internal - Require Basic Auth)
        # =====================================================================
        # Define patterns for internal/admin endpoints that use Basic authentication.
        self.internal_paths_compiled = self._compile_patterns(
            [
                "/api/internal/{path:path}",  # Internal admin endpoints
            ]
        )
        logger.info(
            f"Auth middleware initialized with Starlette routing: "
            f"{len(self.public_paths_compiled)} public, "
            f"{len(self.internal_paths_compiled)} internal, "
        )

        # Pre-load scopes config once for performance (cached at module level)
        self.scopes_config = load_scopes_config()
        logger.info(f"Scopes config loaded with {len(self.scopes_config.get('group_mappings', {}))} group mappings")

    def _compile_patterns(self, patterns: list[str]) -> list[tuple]:
        """
        Compile path patterns into Starlette route matchers
        """
        compiled = []
        for pattern in patterns:
            try:
                path_regex, path_format, param_convertors = compile_path(pattern)
                compiled.append((pattern, path_regex, path_format, param_convertors))
                logger.debug(f"Compiled pattern: {pattern} -> {path_regex.pattern}")
            except Exception as e:
                logger.error(f"Failed to compile pattern '{pattern}': {e}")
        return compiled

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Check authenticated paths first (these override public patterns)
        if self._match_path(path, self.public_paths_compiled):
            logger.debug(f"Public path: {path}")
            return await call_next(request)
        else:
            logger.debug(f"Authenticated path: {path}")
            # Continue to authentication logic below

        # Use context manager for clean metrics tracking
        async with AuthMetricsContext() as auth_ctx:
            try:
                user_context = await self._authenticate(request)
                request.state.user = user_context
                request.state.is_authenticated = True
                auth_source = user_context.get("auth_source", "unknown")
                request.state.auth_source = auth_source

                # Update metrics context with auth result
                auth_ctx.set_mechanism(auth_source)
                auth_ctx.set_success(True)

                logger.info(f"User {user_context.get('username')} authenticated via {auth_source}")
                return await call_next(request)

            except AuthenticationError as e:
                auth_ctx.set_success(False)
                logger.warning(f"Auth failed for {path}: {e}")

                # Add WWW-Authenticate header for MCP OAuth discovery
                # Extract server name from path for MCP proxy requests
                server_name = None
                if path.startswith("/proxy/"):
                    server_name = path.split("/")[2] if len(path.split("/")) > 2 else None

                headers = {"Connection": "close"}
                if server_name:
                    # For MCP proxy paths, RFC 9728 (OAuth 2.0 Protected Resource Metadata)
                    registry_url = settings.registry_client_url.rstrip("/")
                    oauth_discovery = f"{registry_url}/.well-known/oauth-protected-resource/proxy/{server_name}"
                    headers["WWW-Authenticate"] = f'Bearer realm="mcp-registry", resource_metadata="{oauth_discovery}"'
                else:
                    # For other authenticated paths, use general OAuth discovery
                    headers["WWW-Authenticate"] = 'Bearer realm="mcp-registry"'

                return JSONResponse(status_code=401, content={"detail": str(e)}, headers=headers)

            except Exception as e:
                auth_ctx.set_success(False)
                logger.error(f"Auth error for {path}: {e}")
                return JSONResponse(status_code=500, content={"detail": "Authentication error"})

    def _match_path(self, path: str, compiled_patterns: list[tuple]) -> bool:
        """
        Match path using Starlette route matcher
        """
        for original_pattern, path_regex, _path_format, _param_convertors in compiled_patterns:
            match = path_regex.match(path)
            if match:
                logger.debug(f"Path '{path}' matched pattern '{original_pattern}'")
                return True
        return False

    async def _authenticate(self, request: Request) -> UserContextDict:
        """
        Unified authentication logic (simple and efficient)

        1. Internal paths (/api/internal/*) → Basic Auth
        2. Authenticated paths (including /api/auth/me, /api/servers/*, /proxy/*, /api/mcp/*) → JWT or Session Auth
        3. Other paths → Session Auth
        """
        path = request.url.path

        if self._match_path(path, self.internal_paths_compiled):
            user_context = self._try_basic_auth(request)
            if user_context:
                return user_context
            raise AuthenticationError("Basic authentication required")
        # Try JWT first, then fall back to session auth
        user_context = self._try_jwt_auth(request)
        if user_context:
            return user_context
        user_context = await self._try_session_auth(request)
        if user_context:
            return user_context
        raise AuthenticationError("JWT or session authentication required")

    def _try_basic_auth(self, request: Request) -> UserContextDict | None:
        """Basic authentication for internal endpoints"""
        try:
            # Get Authorization header
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Basic "):
                return None

            # Decode Basic Auth credentials
            try:
                encoded_credentials = auth_header.split(" ")[1]
                decoded_credentials = base64.b64decode(encoded_credentials).decode("utf-8")
                username, password = decoded_credentials.split(":", 1)
            except (IndexError, ValueError, Exception) as e:
                logger.debug(f"Basic auth decoding failed: {e}")
                return None

            # Verify admin credentials from environment
            admin_user = os.environ.get("ADMIN_USER", "admin")
            admin_password = os.environ.get("ADMIN_PASSWORD")

            if not admin_password:
                logger.error("ADMIN_PASSWORD environment variable not set")
                return None

            if username != admin_user or password != admin_password:
                logger.debug(f"Basic auth failed: invalid credentials for {username}")
                return None
            # Return user context for admin user
            return self._build_user_context(
                username=username,
                groups=["registry-admin"],
                scopes=["registry-admin"],
                auth_method="basic",
                provider="basic",
                auth_source="basic_auth",
            )

        except Exception as e:
            logger.debug(f"Basic auth failed: {e}")
            return None

    def _try_jwt_auth(self, request: Request) -> UserContextDict | None:
        """JWT token authentication for /api/servers endpoints"""
        try:
            # Get Authorization header
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                logger.debug("Missing Authorization header for JWT auth")
                return None

            access_token = auth_header.split(" ")[1]
            if not access_token:
                logger.debug("Empty JWT token after split")
                return None

            # Extract kid from header first
            try:
                kid = get_token_kid(access_token)
            except Exception as e:
                logger.debug(f"Failed to decode JWT header: {e}")
                return None

            # Check if this is our self-signed token; reject tokens with an unrecognised explicit kid
            if kid and kid != settings.JWT_SELF_SIGNED_KID:
                logger.debug(f"JWT token has wrong kid: {kid}, expected: {settings.JWT_SELF_SIGNED_KID}")
                return None

            # Validate and decode token
            try:
                # For self-signed tokens (kid='mcp-self-signed'), skip audience validation
                # because the audience is now the resource URL (RFC 8707 Resource Indicators)
                # which varies per endpoint (/proxy/mcpgw, /proxy/server2, etc.)
                # Issuer validation provides sufficient security for self-signed tokens.
                is_self_signed_token = kid == settings.JWT_SELF_SIGNED_KID

                if is_self_signed_token:
                    logger.info("Skipping audience validation for self-signed token (RFC 8707 Resource Indicators)")

                claims = decode_jwt(
                    access_token,
                    settings.secret_key,
                    issuer=settings.JWT_ISSUER,
                    audience=None if is_self_signed_token else settings.JWT_AUDIENCE,
                )
                logger.info(
                    f"JWT claims validated: sub={claims.get('sub')}, aud={claims.get('aud')}, scope={claims.get('scope')}"
                )
            except ExpiredSignatureError:
                logger.debug("JWT token has expired")
                return None
            except InvalidTokenError as e:
                logger.debug(f"Invalid JWT token: {e}")
                return None

            # Extract user information from claims
            username = claims.get("sub", "")
            if not username:
                logger.debug("JWT token missing 'sub' claim")
                return None

            # Extract groups first
            groups = claims.get("groups", [])

            # Extract scopes from space-separated string
            scope_string = claims.get("scope", "")
            scopes = scope_string.split() if scope_string else []

            # If no scopes but has groups, map groups to scopes
            if not scopes and groups:
                scopes = map_groups_to_scopes(groups, self.scopes_config)
                logger.info(f"Mapped JWT groups {groups} to scopes: {scopes}")

            # Verify we have at least some scopes
            if not scopes:
                logger.debug(f"JWT token has no scopes and groups mapping failed. Groups: {groups}")
                return None
            # Optional: Verify client_id if present
            client_id = claims.get("client_id")
            if client_id and client_id != "user-generated":
                logger.debug(f"JWT token has unexpected client_id: {client_id}")

            # Log token validation success with additional details
            token_type = claims.get("token_type", "unknown")
            description = claims.get("description", "")
            logger.info(f"JWT token validated for user: {username}, type: {token_type}, scopes: {scopes}")
            if description:
                logger.debug(f"Token description: {description}")
            user_id = claims.get("user_id")
            logger.debug(f"jwt enhencement user id {user_id}")
            # Return user context similar to _try_basic_auth
            return self._build_user_context(
                user_id=user_id,
                username=username,
                groups=groups,
                scopes=scopes,
                auth_method="jwt",
                provider="jwt",
                auth_source="jwt_auth",
            )
        except Exception as e:
            logger.debug(f"JWT auth failed: {e}")
            return None

    async def _try_session_auth(self, request: Request) -> UserContextDict | None:
        """JWT-based session authentication from httpOnly cookie"""
        try:
            session_cookie = request.cookies.get(settings.session_cookie_name)
            if not session_cookie:
                return None

            # Verify JWT access token
            claims = verify_access_token(session_cookie)

            if not claims:
                # Access token invalid or expired - return None to trigger 401
                logger.debug("Access token expired or invalid")
                return None

            # Valid access token - extract user info and build context
            username = claims.get("sub")
            user_id = claims.get("user_id")
            groups = claims.get("groups", [])
            auth_method = claims.get("auth_method", "traditional")

            # Extract scopes from JWT (space-separated string)
            scope_string = claims.get("scope", "")
            scopes = scope_string.split() if scope_string else []

            # If no scopes but has groups, map groups to scopes
            if not scopes and groups:
                scopes = map_groups_to_scopes(groups, self.scopes_config)
                logger.info(f"Mapped session groups {groups} to scopes: {scopes}")

            logger.debug(f"JWT access token valid for user {username} (user_id: {user_id})")

            return self._build_user_context(
                username=username,
                groups=groups,
                scopes=scopes,
                auth_method=auth_method,
                provider=claims.get("provider", "local"),
                auth_source="jwt_session_auth",
                user_id=user_id,
            )

        except Exception as e:
            logger.debug(f"JWT session auth failed: {e}")
            return None

    def _build_user_context(
        self,
        username: str,
        groups: list,
        scopes: list,
        auth_method: str,
        provider: str,
        auth_source: str,
        user_id: str | None = None,
    ) -> UserContextDict:
        """
        Construct the complete user context (from the original enhanced_auth logic).
        """
        user_context = {
            "user_id": user_id,
            "username": username,
            "groups": groups,
            "scopes": scopes,
            "auth_method": auth_method,
            "provider": provider,
            "auth_source": auth_source,
        }
        logger.debug(f"User context for {username}: {user_context}")
        return user_context


class AuthenticationError(Exception):
    pass
