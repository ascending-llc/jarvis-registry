import logging
from datetime import datetime

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from jwt import DecodeError, ExpiredSignatureError, InvalidIssuerError, InvalidSignatureError
from starlette.exceptions import HTTPException

from registry_pkgs.core.jwt_utils import decode_jwt

from ..config import settings

logger = logging.getLogger(__name__)


class HeaderSwapMiddleware(Middleware):
    """
    Middleware to swap custom authentication header to Authorization header.

    This allows FastMCP to process tokens from custom headers like X-Jarvis-Auth.
    Must be added BEFORE AuthMiddleware in the middleware stack.

    Usage:
        mcp.add_middleware(HeaderSwapMiddleware(custom_header="X-Jarvis-Auth"))
        mcp.add_middleware(AuthMiddleware())
    """

    def __init__(self, custom_header: str | None = None):
        """
        Initialize the header swap middleware.

        Args:
            custom_header: Header name to swap to Authorization (default: settings.INTERNAL_AUTH_HEADER)
        """
        super().__init__()
        self.custom_header = (custom_header or settings.INTERNAL_AUTH_HEADER).lower()
        logger.debug(f"HeaderSwapMiddleware initialized: {self.custom_header} -> Authorization")

    async def on_request(self, context: MiddlewareContext, call_next):
        """
        Swap custom header to Authorization before FastMCP processes the request.
        """
        try:
            request = get_http_request()
            headers = dict(request.headers)

            auth_header = "authorization"

            # Check if custom header exists
            if self.custom_header in headers:
                # Preserve original Authorization header if it exists
                if auth_header in headers:
                    headers["x-original-authorization"] = headers[auth_header]
                    logger.debug("Preserved original Authorization -> X-Original-Authorization")

                # Move custom header to Authorization
                headers[auth_header] = headers[self.custom_header]
                logger.debug(f"Swapped {self.custom_header} -> Authorization")

                # Update request headers
                request._headers = headers

        except Exception as e:
            logger.warning(f"Failed to swap auth headers: {e}")

        return await call_next(context)


class AuthMiddleware(Middleware):
    """
    Authentication and authorization middleware for MCP Gateway.

    Handles JWT token verification and user context extraction.
    Allows "initialize" handshake without auth, but protects all other methods.
    """

    def __init__(self, allowed_methods_without_auth: list[str] | None = None):
        """
        Initialize the authentication middleware.

        Args:
            allowed_methods_without_auth: List of MCP methods that don't require auth
        """
        super().__init__()
        self.allowed_methods_without_auth = allowed_methods_without_auth or [
            "initialize",
            "ping",
            "notifications/initialized",
        ]
        logger.info(f"AuthMiddleware initialized: exempt_methods={self.allowed_methods_without_auth}")

    async def on_request(self, context: MiddlewareContext, call_next):
        """
        Process MCP requests with authentication.

        Verifies JWT tokens and extracts user context for authenticated requests.
        Allows exempt methods (initialize, ping) without authentication.
        """
        method = context.method
        logger.debug(f"MCP request: {method}")

        # Allow exempt methods without authentication
        if method not in self.allowed_methods_without_auth:
            # Extract token from headers
            token = await self._extract_token()

            if not token:
                logger.debug(f"No auth token provided for {method}")
                raise HTTPException(status_code=401, detail="Authentication required")

            # Verify JWT token
            user_context = await self._verify_jwt_token(token)

            if not user_context:
                logger.warning(f"Invalid token for {method}")
                raise HTTPException(status_code=401, detail="Invalid authentication token")

            # Store user context for downstream tools
            await self._store_auth_context(context, user_context)

        try:
            result = await call_next(context)
            return result
        except Exception as e:
            logger.error(f"Error processing request {method}: {type(e).__name__}: {e}")
            raise

    async def _extract_token(self) -> str | None:
        """
        Extract JWT token from Authorization header.

        HeaderSwapMiddleware should run before this to swap custom headers.

        Returns:
            JWT token string (without "Bearer " prefix) or None
        """
        try:
            request = get_http_request()
            headers = dict(request.headers)

            # Check Authorization header (HeaderSwapMiddleware already swapped custom headers)
            auth_header = headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                logger.debug("Token extracted from authorization header")
                return token

            return None

        except Exception as e:
            logger.error(f"Failed to extract token: {e}")
            return None

    async def _verify_jwt_token(self, token: str) -> dict | None:
        """
        Verify JWT token and extract claims.

        Args:
            token: JWT token string (without "Bearer " prefix)

        Returns:
            Dictionary with user context (JWT claims) or None if verification fails
        """
        if not token:
            return None

        try:
            # No audience — mcpgw skips aud validation for self-signed tokens.
            claims = decode_jwt(token, settings.SECRET_KEY, settings.JWT_ISSUER)

            if not claims.get("sub"):
                logger.warning("JWT missing required 'sub' claim")
                return None

            # Extract user information
            user_context = dict(claims)

            # Parse scopes from "scope" claim (space-separated string)
            if "scope" in claims:
                scope_str = claims["scope"]
                if isinstance(scope_str, str):
                    user_context["scopes"] = scope_str.split()
            elif "scopes" in claims:
                user_context["scopes"] = claims["scopes"]
            else:
                user_context["scopes"] = []

            # Get user_id from claims or x-user-id header
            if not user_context.get("user_id"):
                request = get_http_request()
                x_user_id = request.headers.get("x-user-id")
                if x_user_id:
                    user_context["user_id"] = x_user_id
                    logger.debug(f"Extracted user_id from x-user-id header: {x_user_id}")
                else:
                    logger.warning("user_id missing in JWT claims and x-user-id header")

            # Log successful authentication
            exp_time = datetime.fromtimestamp(claims["exp"]) if claims.get("exp") else None
            logger.info(
                f"JWT verified: user={claims.get('sub')}, "
                f"client_id={claims.get('client_id')}, "
                f"scopes={len(user_context.get('scopes', []))}, "
                f"expires={exp_time}"
            )

            return user_context

        except ExpiredSignatureError:
            logger.warning("JWT token has expired")
            return None
        except InvalidIssuerError:
            logger.warning(f"Invalid token issuer. Expected: {settings.JWT_ISSUER}")
            return None
        except InvalidSignatureError:
            logger.warning("Invalid JWT signature")
            return None
        except DecodeError as e:
            logger.error(f"Failed to decode JWT token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during JWT validation: {e}", exc_info=True)
            return None

    async def _store_auth_context(self, context: MiddlewareContext, user_context: dict) -> None:
        """
        Store authentication context for downstream tools.

        Args:
            context: Middleware context
            user_context: Dictionary with user authentication context
        """
        try:
            ctx = context.fastmcp_context
            if not hasattr(ctx, "user_auth"):
                ctx.user_auth = {}

            # Store full user context
            ctx.user_auth.update(user_context)

            logger.debug(
                f"Stored user context: username={user_context.get('username')}, "
                f"scopes={len(user_context.get('scopes', []))}, "
                f"groups={len(user_context.get('groups', []))}"
            )
        except Exception as e:
            logger.warning(f"Could not store auth context: {type(e).__name__}: {e}")
