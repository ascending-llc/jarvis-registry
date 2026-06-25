import logging
import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import compile_path, get_route_path

from registry_pkgs.core.jwt_tokens import verify_managed_agent_token
from registry_pkgs.core.jwt_utils import ExpiredSignatureError, InvalidTokenError
from registry_pkgs.core.scopes import map_groups_to_scopes

from ..auth.dependencies import UserContextDict
from ..core.config import settings
from ..core.telemetry_decorators import AuthMetricsContext
from ..utils.crypto_utils import verify_access_token

logger = logging.getLogger(__name__)

# Direct-connect proxy path: /proxy/server/{user_id}/{server_path}. Used to bind a managed-agent
# token's direct-connect claims to the URL.
DIRECT_CONNECT_RE = re.compile(r"^/proxy/server/([^/]+)/(.+)$")


def _parse_bearer_token(request: Request) -> str | None:
    """Extract a non-empty Bearer token from the Authorization header, or None."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer":
        return None

    return token.strip() or None


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
        self.public_paths_compiled = self._compile_patterns(
            [
                "/",
                "/login",
                "/health",
                "/docs",
                "/redoc",
                "/openapi.json",
                "/static/{path:path}",
                "/redirect",
                "/redirect/{provider}",
                "/api/auth/providers",
                "/api/auth/config",
                f"/api/{settings.api_version}/mcp/{{server_name}}/oauth/callback",  # OAuth callback is public
                f"/api/{settings.api_version}/mcp/downstream/oauth/token/{{user_id}}/{{server_path:path}}",
                "/.well-known/{path:path}",  # OAuth discovery endpoints must be public
            ]
        )

        logger.info(f"Auth middleware initialized with Starlette routing: {len(self.public_paths_compiled)} public.")

        # Pre-load scopes config once for performance (cached at module level)
        self.scopes_config = settings.scopes_config
        logger.info(f"Scopes config loaded with {len(self.scopes_config.get('group_mappings', {}))} group mappings")

    def _is_proxy_route(self, path: str) -> bool:
        """Single source of truth for the proxy/non-proxy split."""
        return path.startswith("/proxy/")

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
        # Use get_route_path to strip the root_path prefix (set by uvicorn --root-path).
        # request.url.path reads scope["path"] directly, which includes the prefix when
        # uvicorn is started with --root-path. get_route_path strips it, matching what
        # the router itself sees when resolving routes.
        path = get_route_path(request.scope)

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
                user_context = await self._authenticate(request, path)
                request.state.user = user_context
                request.state.is_authenticated = True
                auth_source = user_context.get("auth_source", "unknown")
                request.state.auth_source = auth_source

                # Update metrics context with auth result
                auth_ctx.set_mechanism(auth_source)
                auth_ctx.set_success(True)

                logger.info(f"User {user_context.get('username')} authenticated via {auth_source}")

            except AuthenticationError as e:
                auth_ctx.set_success(False)

                logger.warning(f"Auth failed for {path}")

                headers = {"Connection": "close"}

                if self._is_proxy_route(path):
                    # Proxy routes are Bearer-authenticated (managed-agent tokens). Advertise a
                    # Bearer challenge with resource metadata so AI agents can perform Dynamic
                    # Client Registration.
                    headers["WWW-Authenticate"] = (
                        f'Bearer realm="{settings.jarvis_realm}", '
                        f'resource_metadata="{settings.jwt_issuer}/.well-known/oauth-protected-resource{settings.service_base_path}{path}", '
                        'scope="mcp-proxy-ops"'
                    )
                # Non-proxy routes are cookie-authenticated (CRUD-session cookie). Cookie/session
                # auth has no RFC 7235 challenge scheme, and the only caller is our frontend, which
                # handles a bare 401 by redirecting to login — so we deliberately advertise no
                # (misleading) Bearer challenge here.

                return JSONResponse(status_code=401, content={"detail": str(e)}, headers=headers)

            except Exception as e:
                auth_ctx.set_success(False)
                logger.exception(f"Auth error for {path}: {e}")
                return JSONResponse(status_code=500, content={"detail": "Authentication error"})

        return await call_next(request)

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

    async def _authenticate(self, request: Request, path: str) -> UserContextDict:
        """Route-based authentication dispatch.

        - Proxy routes (``/proxy/*``): the ONLY accepted credential is a managed-agent
          Bearer token in the Authorization header. The session cookie is never consulted.
        - Every other authenticated route: the ONLY accepted credential is the
          CRUD-session cookie. The Authorization header is never consulted.

        This hard split is what stops a leaked managed-agent token (e.g. via the DCR-CSRF
        path) from being replayed as a dashboard session cookie, and vice versa.
        """
        if self._is_proxy_route(path):
            user_context = self._try_jwt_auth(request, path)
            if user_context:
                return user_context
            raise AuthenticationError("Managed-agent Bearer token required for proxy routes")

        user_context = await self._try_session_auth(request)
        if user_context:
            return user_context
        raise AuthenticationError("Session authentication required")

    def _try_jwt_auth(self, request: Request, path: str) -> UserContextDict | None:
        """Bearer-token authentication for proxy routes.

        Accepts a managed-agent token (the proxy credential, including the access token issued by the
        direct-connect downstream ``/token`` endpoint). On direct-connect routes, the token's
        ``user_id`` and ``server_path`` must match the URL.
        """
        access_token = _parse_bearer_token(request)
        if access_token is None:
            return None

        try:
            claims = self._verify_managed_agent_claims(access_token)
            if claims is not None:
                return self._build_managed_agent_context(claims, path)
            return None
        except Exception as e:
            logger.debug(f"JWT auth failed: {e}")
            return None

    @staticmethod
    def _verify_managed_agent_claims(access_token: str) -> dict | None:
        """Validate a managed-agent (proxy) token. Returns its claims, or None if it is not one.

        Wrong class/audience/kid/client_id all raise InvalidTokenError and are treated as "not a
        usable token here".
        """
        try:
            claims = verify_managed_agent_token(settings.jwt_token_config, access_token)
        except (ExpiredSignatureError, InvalidTokenError) as e:
            logger.debug(f"Not a valid managed-agent token: {e}")
            return None

        logger.info(
            f"Managed-agent token validated: sub={claims.get('sub')}, "
            f"aud={claims.get('aud')}, client_id={claims.get('client_id')}"
        )
        return claims

    def _build_managed_agent_context(self, claims: dict, path: str) -> UserContextDict | None:
        """Build a user context from validated managed-agent claims, enforcing direct-connect binding."""
        username = claims.get("sub", "")
        if not username:
            logger.debug("JWT token missing 'sub' claim")
            return None

        groups = claims.get("groups", [])

        scope_string = claims.get("scope", "")
        scopes = scope_string.split() if scope_string else []

        if not scopes and groups:
            scopes = map_groups_to_scopes(groups, settings.scopes_file_config)
            logger.info(f"Mapped JWT groups {groups} to scopes: {scopes}")

        if not scopes:
            logger.debug(f"JWT token has no scopes and groups mapping failed. Groups: {groups}")
            return None

        user_id = claims.get("user_id")

        binding = DIRECT_CONNECT_RE.match(path)
        if binding is not None:
            url_user_id = binding.group(1)
            if user_id != url_user_id:
                logger.warning(f"user_id mismatch: token has {user_id}, URL has {url_user_id}")
                return None
            url_server_path = binding.group(2)
            token_server_path = claims.get("server_path")
            if token_server_path != url_server_path:
                logger.warning(f"server_path mismatch: token has {token_server_path}, URL has {url_server_path}")
                return None

        token_class = claims.get("token_class", "unknown")
        logger.info(f"Managed-agent token validated for user: {username}, class: {token_class}, scopes: {scopes}")

        return self._build_user_context(
            user_id=user_id,
            username=username,
            groups=groups,
            scopes=scopes,
            auth_method="jwt",
            provider="jwt",
            auth_source="jwt_auth",
        )

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
                scopes = map_groups_to_scopes(groups, settings.scopes_file_config)
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
        username: str | None,
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
        user_context: UserContextDict = {
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
