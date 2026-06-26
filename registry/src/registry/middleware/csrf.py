import hmac
import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import get_route_path

from ..core.config import settings
from ..utils.csrf import compute_csrf_token

logger = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce HMAC double-submit CSRF protection for browser session requests."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method.upper() in SAFE_METHODS:
            return await call_next(request)

        session_cookie = request.cookies.get(settings.session_cookie_name)
        if not session_cookie:
            return await call_next(request)

        header_value = request.headers.get(settings.csrf_header_name)
        if not header_value:
            path = get_route_path(request.scope)
            logger.warning("CSRF token missing for %s %s", request.method, path)
            return JSONResponse(status_code=403, content={"detail": "CSRF token missing"})

        expected = compute_csrf_token(session_cookie)
        if not hmac.compare_digest(expected, header_value):
            path = get_route_path(request.scope)
            logger.warning("CSRF token invalid for %s %s", request.method, path)
            return JSONResponse(status_code=403, content={"detail": "CSRF token invalid"})

        return await call_next(request)
