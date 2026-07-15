"""Unit tests for OAuth redirect return-path handling."""

import base64
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request
from fastapi.responses import RedirectResponse

from registry.api.redirect_routes import (
    _decode_client_state,
    _sanitize_return_path,
    oauth2_callback,
    oauth2_login_redirect,
)


def _encode_state(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).decode("utf-8").rstrip("=")


@pytest.fixture
def mock_request() -> Mock:
    request = Mock(spec=Request)
    request.base_url = "http://localhost:8000/"
    request.cookies = {}
    request.headers = {}
    request.url = Mock()
    request.url.scheme = "http"
    return request


@pytest.fixture
def mock_settings():
    with patch("registry.api.redirect_routes.settings") as settings:
        settings.auth_server_url = "http://auth.example.com"
        settings.auth_server_external_url = "http://auth.example.com"
        settings.csrf_cookie_name = "csrf"
        settings.entra_group_sync_enabled = False
        settings.jwt_issuer = "test-issuer"
        settings.jwt_public_key = "test-public-key"
        settings.oauth_session_ttl_seconds = 600
        settings.refresh_cookie_name = "refresh"
        settings.registry_app_name = "jarvis-registry-client"
        settings.registry_client_secret = "test-secret"
        settings.registry_client_url = "http://localhost:80/gateway"
        settings.registry_redirect_uri = "http://localhost:7860/redirect"
        settings.registry_url = "http://localhost:7860"
        settings.scopes_file_config = {}
        settings.session_cookie_name = "session"
        settings.session_cookie_secure = False
        yield settings


@pytest.fixture
def mock_user() -> Mock:
    user = Mock()
    user.id = "12345"
    user.username = "testuser"
    user.email = "test@example.com"
    user.role = "user"
    return user


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "/consent/server?nonce=abc",
        "/",
    ],
)
def test_sanitize_return_path_allows_same_origin_paths(raw: str) -> None:
    assert _sanitize_return_path(raw) == raw


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "login",
        "//evil.com/x",
        "/\\evil.com/x",
        "https://evil.com",
        "/x\r\nSet-Cookie: a=b",
    ],
)
def test_sanitize_return_path_rejects_unsafe_values(raw: object) -> None:
    assert _sanitize_return_path(raw) == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (_encode_state({"nonce": "abc", "next": "/server-registry"}), {"nonce": "abc", "next": "/server-registry"}),
        ("", {}),
        ("not base64", {}),
        (_encode_state(["not", "a", "dict"]), {}),
    ],
)
def test_decode_client_state(raw: str, expected: dict[str, object]) -> None:
    assert _decode_client_state(raw) == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oauth2_login_redirect_carries_next_in_state(mock_settings) -> None:
    response = await oauth2_login_redirect("entra", next_path="/consent/server?nonce=abc")

    assert isinstance(response, RedirectResponse)

    location = response.headers["location"]
    state = location.split("state=", 1)[1].split("&", 1)[0]
    decoded = _decode_client_state(state)
    assert decoded["next"] == "/consent/server?nonce=abc"


async def _successful_callback(
    *,
    mock_request: Mock,
    mock_user: Mock,
    state: str | None,
) -> RedirectResponse:
    token_response = Mock()
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "test-access-token"}

    user_service = Mock()
    user_service.get_user_by_user_id = AsyncMock(return_value=mock_user)

    group_service = Mock()
    group_service.sync_user_group_memberships = AsyncMock()

    with (
        patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client,
        patch("registry.api.redirect_routes.decrypt_value", return_value="verifier"),
        patch("registry.api.redirect_routes.decode_jwt", return_value={"sub": "someone", "user_id": "12345"}),
        patch("registry.api.redirect_routes.filter_known_groups", return_value=[]),
        patch(
            "registry.api.redirect_routes.generate_token_pair",
            return_value=("mock-access-token", "mock-refresh-token"),
        ),
    ):
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=token_response)
        response = await oauth2_callback(
            mock_request,
            code="test-auth-code",
            state=state,
            registry_oauth2_code_verifier="a-cookie",
            user_service=user_service,
            group_service=group_service,
        )

    return response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oauth2_callback_redirects_to_safe_next_path(mock_request, mock_settings, mock_user) -> None:
    state = _encode_state({"nonce": "abc", "next": "/consent/server?nonce=abc"})

    response = await _successful_callback(mock_request=mock_request, mock_user=mock_user, state=state)

    assert response.headers["location"] == f"{mock_settings.registry_client_url}/consent/server?nonce=abc"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        None,
        _encode_state({"nonce": "abc"}),
        _encode_state({"nonce": "abc", "next": "https://evil.com"}),
    ],
)
async def test_oauth2_callback_falls_back_to_registry_client_url(
    mock_request,
    mock_settings,
    mock_user,
    state: str | None,
) -> None:
    response = await _successful_callback(mock_request=mock_request, mock_user=mock_user, state=state)

    assert response.headers["location"] == mock_settings.registry_client_url
