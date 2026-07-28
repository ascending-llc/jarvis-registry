"""
Unit tests chaining all three registry security middlewares together.

The wider integration suite globally bypasses RBAC permission checks. These tests
restore the real implementation and use production middleware ordering to verify
CSRF short-circuiting and Auth-to-RBAC request.state handoff.
"""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.testclient import TestClient
from starlette.types import Message, Scope

from registry.app_factory import _configure_middleware
from registry.core.config import settings
from registry.middleware.rbac import ScopePermissionMiddleware
from registry.utils.crypto_utils import generate_access_token
from registry.utils.csrf import compute_csrf_token

_COOKIE = settings.session_cookie_name
_original_has_permission = ScopePermissionMiddleware._has_permission


@pytest.fixture(autouse=True)
def restore_rbac_for_chain_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore real RBAC permission checking for this module."""
    monkeypatch.setattr(ScopePermissionMiddleware, "_has_permission", _original_has_permission)


@pytest.fixture(autouse=True)
def _widgets_scopes_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "scopes_config",
        {
            "widgets-read": [
                {"endpoint": "/widgets", "method": "GET"},
                {"endpoint": "/widgets/disconnect", "method": "GET"},
            ],
            "widgets-write": [{"endpoint": "/widgets", "method": "POST"}],
        },
    )


def _build_app() -> FastAPI:
    app = FastAPI()
    _configure_middleware(app)

    @app.get(f"/api/{settings.api_version}/widgets")
    async def list_widgets() -> JSONResponse:
        return JSONResponse({"ok": "list"})

    @app.post(f"/api/{settings.api_version}/widgets")
    async def create_widget() -> JSONResponse:
        return JSONResponse({"ok": "create"})

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


def _session_cookie(scopes: list[str]) -> str:
    return generate_access_token(
        user_id="u1",
        username="alice",
        email="alice@example.com",
        groups=["g1"],
        scopes=scopes,
        role="user",
        auth_method="oauth2",
        provider="entra",
    )


def _disconnect_scope(session_cookie: str) -> Scope:
    path = f"/api/{settings.api_version}/widgets/disconnect"
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"cookie", f"{_COOKIE}={session_cookie}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


def test_get_with_valid_cookie_and_matching_scope_returns_200(client: TestClient) -> None:
    client.cookies.set(_COOKIE, _session_cookie(["widgets-read"]))

    response = client.get(f"/api/{settings.api_version}/widgets")

    assert response.status_code == 200


def test_post_without_csrf_header_is_blocked_before_auth_or_rbac(client: TestClient) -> None:
    client.cookies.set(_COOKIE, _session_cookie(["widgets-write"]))

    response = client.post(f"/api/{settings.api_version}/widgets")

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF token missing"}


def test_post_with_csrf_header_and_matching_scope_returns_200(client: TestClient) -> None:
    session_cookie = _session_cookie(["widgets-write"])
    client.cookies.set(_COOKIE, session_cookie)

    response = client.post(
        f"/api/{settings.api_version}/widgets",
        headers={settings.csrf_header_name: compute_csrf_token(session_cookie)},
    )

    assert response.status_code == 200


def test_post_with_csrf_header_but_wrong_scope_returns_403(client: TestClient) -> None:
    session_cookie = _session_cookie(["widgets-read"])
    client.cookies.set(_COOKIE, session_cookie)

    response = client.post(
        f"/api/{settings.api_version}/widgets",
        headers={settings.csrf_header_name: compute_csrf_token(session_cookie)},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}


def test_get_without_cookie_is_rejected_by_auth_despite_safe_method(client: TestClient) -> None:
    response = client.get(f"/api/{settings.api_version}/widgets")

    assert response.status_code == 401


async def test_client_disconnect_does_not_raise_cancel_scope_runtime_error() -> None:
    app = _build_app()
    waiting_for_disconnect = asyncio.Event()
    disconnect = asyncio.Event()
    sent_messages: list[Message] = []
    receive_count = 0

    @app.get(f"/api/{settings.api_version}/widgets/disconnect")
    async def wait_for_disconnect(request: Request) -> Response:
        while (await request.receive())["type"] != "http.disconnect":
            pass
        return Response(status_code=204)

    session_cookie = _session_cookie(["widgets-read"])
    scope = _disconnect_scope(session_cookie)

    async def receive() -> Message:
        nonlocal receive_count
        receive_count += 1
        if receive_count == 1:
            return {"type": "http.request", "body": b"", "more_body": False}

        waiting_for_disconnect.set()
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent_messages.append(message)

    request_task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(waiting_for_disconnect.wait(), timeout=1)
        disconnect.set()
        await asyncio.wait_for(request_task, timeout=1)
    finally:
        if not request_task.done():
            request_task.cancel()
            try:
                await request_task
            except asyncio.CancelledError:
                pass

    response_start = next(message for message in sent_messages if message["type"] == "http.response.start")
    assert response_start["status"] == 204
