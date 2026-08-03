import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.routing import get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send

from ..core.config import settings
from ..utils.csrf import compute_csrf_token

logger = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class CSRFMiddleware:
    """Enforce HMAC double-submit CSRF protection for browser session requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)

        if request.method.upper() in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        session_cookie = request.cookies.get(settings.session_cookie_name)
        if not session_cookie:
            await self.app(scope, receive, send)
            return

        header_value = request.headers.get(settings.csrf_header_name)
        if not header_value:
            path = get_route_path(request.scope)
            logger.warning("CSRF token missing for %s %s", request.method, path)
            response = JSONResponse(status_code=403, content={"detail": "CSRF token missing"})
            await response(scope, receive, send)
            return

        expected = compute_csrf_token(session_cookie)
        if not hmac.compare_digest(expected, header_value):
            path = get_route_path(request.scope)
            logger.warning("CSRF token invalid for %s %s", request.method, path)
            response = JSONResponse(status_code=403, content={"detail": "CSRF token invalid"})
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
