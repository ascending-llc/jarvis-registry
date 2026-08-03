from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from registry.app_factory import _configure_middleware
from registry.core.config import settings
from registry.middleware.auth import UnifiedAuthMiddleware
from registry.middleware.csrf import CSRFMiddleware
from registry.middleware.rbac import ScopePermissionMiddleware
from registry.utils.csrf import compute_csrf_token


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.api_route("/resource", methods=["GET", "POST", "OPTIONS"])
    async def resource():
        return JSONResponse({"ok": True})

    return app


def test_get_with_session_cookie_no_csrf_header_returns_200():
    client = TestClient(_build_app())
    client.cookies.set(settings.session_cookie_name, "session-token")

    response = client.get("/resource")

    assert response.status_code == 200


def test_post_without_session_cookie_no_csrf_header_returns_200():
    client = TestClient(_build_app())

    response = client.post("/resource")

    assert response.status_code == 200


def test_post_with_session_cookie_correct_csrf_header_returns_200():
    client = TestClient(_build_app())
    session_cookie = "session-token"
    client.cookies.set(settings.session_cookie_name, session_cookie)

    response = client.post("/resource", headers={settings.csrf_header_name: compute_csrf_token(session_cookie)})

    assert response.status_code == 200


def test_post_with_session_cookie_missing_csrf_header_returns_403():
    client = TestClient(_build_app())
    client.cookies.set(settings.session_cookie_name, "session-token")

    response = client.post("/resource")

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF token missing"}


def test_post_with_session_cookie_wrong_csrf_header_returns_403():
    client = TestClient(_build_app())
    client.cookies.set(settings.session_cookie_name, "session-token")

    response = client.post("/resource", headers={settings.csrf_header_name: "wrong-token"})

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF token invalid"}


def test_post_with_tampered_session_cookie_returns_403():
    client = TestClient(_build_app())
    original_session_cookie = "session-token"
    client.cookies.set(settings.session_cookie_name, "tampered-session-token")

    response = client.post(
        "/resource", headers={settings.csrf_header_name: compute_csrf_token(original_session_cookie)}
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF token invalid"}


def test_options_with_session_cookie_no_csrf_header_returns_200():
    client = TestClient(_build_app())
    client.cookies.set(settings.session_cookie_name, "session-token")

    response = client.options("/resource")

    assert response.status_code == 200


def test_configured_middleware_order_runs_cors_then_csrf_then_auth():
    app = FastAPI()

    _configure_middleware(app)

    middleware_order = [middleware.cls for middleware in app.user_middleware]
    assert middleware_order == [
        CORSMiddleware,
        CSRFMiddleware,
        UnifiedAuthMiddleware,
        ScopePermissionMiddleware,
    ]


def test_lifespan_scope_passes_through_without_error():
    with TestClient(_build_app()):
        pass


async def test_non_http_scope_forwarded_to_app_unchanged():
    calls: list[Scope] = []

    async def stub_app(scope: Scope, receive: Receive, send: Send) -> None:
        del receive, send
        calls.append(scope)

    middleware = CSRFMiddleware(stub_app)
    scope: Scope = {"type": "websocket", "path": "/ws"}

    await middleware(scope, AsyncMock(), AsyncMock())

    assert calls == [scope]
