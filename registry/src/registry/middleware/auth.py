import logging
import re
from urllib.parse import quote

from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import compile_path, get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send

from registry_pkgs.core.jwt_tokens import verify_managed_agent_token
from registry_pkgs.core.jwt_utils import ExpiredSignatureError, InvalidTokenError
from registry_pkgs.core.scopes import map_groups_to_scopes

from ..auth.dependencies import UserContextDict
from ..constants import MAX_RETURN_PATH_LENGTH, OAUTH_AUTHORIZE_RETURN_URL_TOO_LONG_DETAIL
from ..core.config import settings
from ..core.telemetry_decorators import AuthMetricsContext
from ..utils.crypto_utils import verify_access_token

logger = logging.getLogger(__name__)

# Direct-connect proxy path: /proxy/server/{user_id}/{server_path}. Used to bind a managed-agent
# token's direct-connect claims to the URL.
DIRECT_CONNECT_RE = re.compile(r"^/proxy/server/([^/]+)/(.+)$")
SKILLS_API_RE = re.compile(r"^/api/v[^/]+/skills(?:/|$)")
A2A_PROXY_RE = re.compile(r"^/proxy/a2a(?:/|$)")
DUAL_AUTH_SKILL_READ_PATTERNS = (
    re.compile(r"^/api/v[^/]+/skills$"),
    re.compile(r"^/api/v[^/]+/skills/[^/]+/content$"),
)


def _parse_bearer_token(request: Request) -> str | None:
    """Extract a non-empty Bearer token from the Authorization header, or None."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer":
        return None

    return token.strip() or None


def _required_bearer_scope(path: str) -> str:
    if SKILLS_API_RE.match(path):
        return "skills-read"
    if A2A_PROXY_RE.match(path):
        return "a2a-proxy-ops"
    return "mcp-proxy-ops"


class UnifiedAuthMiddleware:
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

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
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
                f"/api/{settings.api_version}/skill-sync-sources/{{source_id}}/oauth/callback",
                f"/api/{settings.api_version}/mcp/downstream/oauth/device/{{user_id}}/{{server_path:path}}",
                f"/api/{settings.api_version}/mcp/downstream/oauth/token/{{user_id}}/{{server_path:path}}",
                f"/api/{settings.api_version}/mcp/consent/device/resolve",
                "/proxy/a2a/{agent_path}/agent-card.json",
                "/proxy/a2a/{agent_path}/.well-known/agent-card.json",
                "/.well-known/{path:path}",  # OAuth discovery endpoints must be public
            ]
        )
        self.soft_auth_paths_compiled = self._compile_patterns(
            [f"/api/{settings.api_version}/mcp/downstream/oauth/authorize/{{user_id}}/{{server_path:path}}"]
        )

        logger.info(f"Auth middleware initialized with Starlette routing: {len(self.public_paths_compiled)} public.")

        # Pre-load scopes config once for performance (cached at module level)
        self.scopes_config = settings.scopes_config
        logger.info(f"Scopes config loaded with {len(self.scopes_config.get('group_mappings', {}))} group mappings")

    def _is_proxy_route(self, path: str) -> bool:
        """Single source of truth for the proxy/non-proxy split."""
        return path.startswith("/proxy/")

    def _is_downstream_authorize_route(self, request: Request, path: str) -> bool:
        """Return whether this GET authorize entrypoint is eligible for soft authentication."""
        return request.method == "GET" and self._match_path(path, self.soft_auth_paths_compiled)

    def _build_soft_auth_response(self, request: Request, path: str) -> Response:
        """Build a login redirect, or reject a return target too large to round-trip safely."""
        next_path = f"{path}?{request.url.query}" if request.url.query else path
        if len(next_path) > MAX_RETURN_PATH_LENGTH:
            return JSONResponse(
                status_code=status.HTTP_414_URI_TOO_LONG,
                content={"detail": OAUTH_AUTHORIZE_RETURN_URL_TOO_LONG_DETAIL},
            )
        login_url = f"{settings.registry_client_url}/login?next={quote(next_path, safe='')}"
        return RedirectResponse(url=login_url, status_code=302)

    @staticmethod
    def _is_dual_auth_skill_read(request: Request, path: str) -> bool:
        """Return whether this is one of the two CLI sync-down skill endpoints."""
        return request.method == "GET" and any(pattern.fullmatch(path) for pattern in DUAL_AUTH_SKILL_READ_PATTERNS)

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

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        # Use get_route_path to strip the root_path prefix (set by uvicorn --root-path).
        # request.url.path reads scope["path"] directly, which includes the prefix when
        # uvicorn is started with --root-path. get_route_path strips it, matching what
        # the router itself sees when resolving routes.
        path = get_route_path(request.scope)

        # Check authenticated paths first (these override public patterns)
        if self._match_path(path, self.public_paths_compiled):
            logger.debug(f"Public path: {path}")
            await self.app(scope, receive, send)
            return
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

            except SoftAuthRedirect as e:
                auth_ctx.set_success(False)
                await e.response(scope, receive, send)
                return

            except AuthenticationError as e:
                auth_ctx.set_success(False)

                logger.warning(f"Auth failed for {path}")

                headers = {"Connection": "close"}

                if self._is_proxy_route(path) or self._is_dual_auth_skill_read(request, path):
                    # Proxy routes are Bearer-authenticated (managed-agent tokens). Advertise a
                    # Bearer challenge with resource metadata so AI agents can perform Dynamic
                    # Client Registration.
                    headers["WWW-Authenticate"] = (
                        f'Bearer realm="{settings.jarvis_realm}", '
                        f'resource_metadata="{settings.jwt_issuer}/.well-known/oauth-protected-resource{settings.service_base_path}{path}", '
                        f'scope="{_required_bearer_scope(path)}"'
                    )
                # Non-proxy routes are cookie-authenticated (CRUD-session cookie). Cookie/session
                # auth has no RFC 7235 challenge scheme, and the only caller is our frontend, which
                # handles a bare 401 by redirecting to login — so we deliberately advertise no
                # (misleading) Bearer challenge here.

                response = JSONResponse(status_code=401, content={"detail": str(e)}, headers=headers)
                await response(scope, receive, send)
                return

            except Exception as e:
                auth_ctx.set_success(False)
                logger.exception(f"Auth error for {path}: {e}")
                response = JSONResponse(status_code=500, content={"detail": "Authentication error"})
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)

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
        - Skill sync-down reads: session cookie first, then managed-agent Bearer token.
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

        if self._is_downstream_authorize_route(request, path):
            user_context = await self._try_session_auth(request)
            if user_context:
                return user_context
            raise SoftAuthRedirect(self._build_soft_auth_response(request, path))

        if self._is_dual_auth_skill_read(request, path):
            user_context = await self._try_session_auth(request)
            if user_context:
                return user_context
            user_context = self._try_jwt_auth(request, path)
            if user_context:
                return user_context
            raise AuthenticationError("Session or managed-agent authentication required")

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
            # Root-AS tokens (requiresOAuth=False servers) carry no server_path claim; only
            # downstream-AS tokens (requiresOAuth=True) embed one. Skip the binding check when
            # absent so non-OAuth direct-connect servers still work with a root-AS token.
            if token_server_path is not None and token_server_path != url_server_path:
                logger.warning(f"server_path mismatch: token has {token_server_path}, URL has {url_server_path}")
                return None

        token_class = claims.get("token_class", "unknown")
        logger.info(f"Managed-agent token validated for user: {username}, class: {token_class}, scopes: {scopes}")

        return self._build_user_context(
            user_id=user_id,
            client_id=claims.get("client_id", ""),
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
                client_id=claims.get("client_id", ""),
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
        client_id: str,
        user_id: str | None = None,
    ) -> UserContextDict:
        """
        Construct the complete user context (from the original enhanced_auth logic).
        """
        user_context: UserContextDict = {
            "user_id": user_id,
            "client_id": client_id,
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


class SoftAuthRedirect(Exception):
    """Carry a pre-built response for a soft-auth route with no active session."""

    def __init__(self, response: Response) -> None:
        self.response = response
