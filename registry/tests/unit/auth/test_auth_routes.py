"""
Unit tests for authentication routes.
"""

import time
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from bson import ObjectId
from fastapi import Request
from fastapi.responses import RedirectResponse

from registry.api.redirect_routes import (
    get_oauth2_providers,
    logout_post,
    oauth2_callback,
    oauth2_login_redirect,
    refresh_token,
)
from registry.utils.csrf import compute_csrf_token
from registry_pkgs.core.jwt_utils import InvalidSignatureError


@pytest.mark.unit
@pytest.mark.auth
class TestAuthRoutes:
    """Test suite for authentication routes."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock request object."""
        request = Mock(spec=Request)
        request.base_url = "http://localhost:8000/"
        request.cookies = {}
        request.headers = {}
        request.url = Mock()
        request.url.scheme = "http"
        return request

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for testing."""
        with patch("registry.api.redirect_routes.settings") as mock_settings:
            mock_settings.auth_server_url = "http://auth.example.com"
            mock_settings.auth_server_external_url = "http://auth.example.com"
            mock_settings.session_cookie_name = "session"
            mock_settings.refresh_cookie_name = "refresh"
            mock_settings.csrf_cookie_name = "csrf"
            mock_settings.session_max_age_seconds = 3600
            mock_settings.session_cookie_secure = False
            mock_settings.templates_dir = "/templates"
            mock_settings.registry_client_url = "http://localhost:8000"
            mock_settings.registry_redirect_uri = "http://localhost:8000"
            mock_settings.jwt_public_key = "test-public-key"
            mock_settings.jwt_issuer = "test-issuer"
            yield mock_settings

    @pytest.fixture
    def mock_templates(self):
        """Mock Jinja2Templates."""
        with patch("registry.api.redirect_routes.templates") as mock_templates:
            yield mock_templates

    @pytest.mark.asyncio
    async def test_get_oauth2_providers_success(self):
        """Test successful OAuth2 providers fetch."""
        mock_providers = [{"name": "google", "display_name": "Google"}, {"name": "github", "display_name": "GitHub"}]

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"providers": mock_providers}

            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

            providers = await get_oauth2_providers()

            assert providers == mock_providers

    @pytest.mark.asyncio
    async def test_get_oauth2_providers_failure(self):
        """Test OAuth2 providers fetch failure."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("Network error")

            providers = await get_oauth2_providers()

            assert providers == []

    @pytest.mark.asyncio
    async def test_get_oauth2_providers_bad_response(self):
        """Test OAuth2 providers fetch with bad response."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = Mock()
            mock_response.status_code = 404

            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

            providers = await get_oauth2_providers()

            assert providers == []

    # login_form endpoint was removed in refactoring
    # @pytest.mark.asyncio
    # async def test_login_form_success(self, mock_request, mock_templates):
    #     """Test login form rendering."""
    #     mock_providers = [{"name": "google", "display_name": "Google"}]
    #
    #     with patch('registry.api.redirect_routes.get_oauth2_providers') as mock_get_providers:
    #         mock_get_providers.return_value = mock_providers
    #         mock_templates.TemplateResponse.return_value = HTMLResponse("login form")
    #
    #         response = await login_form(mock_request)
    #
    #         mock_templates.TemplateResponse.assert_called_once_with(
    #             "login.html",
    #             {
    #                 "request": mock_request,
    #                 "error": None,
    #                 "oauth_providers": mock_providers
    #             }
    #         )

    # @pytest.mark.asyncio
    # async def test_login_form_with_error(self, mock_request, mock_templates):
    #     """Test login form rendering with error message."""
    #     with patch('registry.api.redirect_routes.get_oauth2_providers') as mock_get_providers:
    #         mock_get_providers.return_value = []
    #
    #         response = await login_form(mock_request, error="Invalid credentials")
    #
    #         mock_templates.TemplateResponse.assert_called_once_with(
    #             "login.html",
    #             {
    #                 "request": mock_request,
    #                 "error": "Invalid credentials",
    #                 "oauth_providers": []
    #             }
    #         )

    @pytest.mark.asyncio
    async def test_oauth2_login_redirect_success(self, mock_settings):
        """Test successful OAuth2 login redirect."""
        provider = "entra"

        response = await oauth2_login_redirect(provider)

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302

        result = urlparse(response.headers["location"])

        assert f"http://{result.netloc}" == mock_settings.auth_server_external_url

        assert result.path == f"/oauth2/login/{provider}"

        qs = parse_qs(result.query)

        assert "state" in qs
        assert qs["redirect_uri"][0] == mock_settings.registry_redirect_uri

    @pytest.fixture
    def mock_code(self):
        """Create a mock authorization code."""
        return "test-auth-code-123"

    @pytest.mark.asyncio
    async def test_oauth2_callback_success(self, mock_request, mock_settings, mock_code):
        """Test successful OAuth2 callback with valid user."""
        mock_user = Mock()
        mock_user.id = "12345"
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.role = "user"
        mock_user.idp_id = "12345-6789"

        # Mock httpx AsyncClient for OAuth token exchange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "test-access-token"}
        mock_user_service = Mock()
        mock_user_service.get_user_by_user_id = AsyncMock(return_value=mock_user)

        user_claims = {
            "sub": "someone",
            "user_id": "12345",
        }

        with (
            patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client,
            patch("registry.api.redirect_routes.decrypt_value") as mock_decrypter,
            patch("registry.api.redirect_routes.decode_jwt") as mock_decoder,
            patch(
                "registry.api.redirect_routes.generate_token_pair",
                return_value=("mock-access-token", "mock-refresh-token"),
            ),
        ):
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_decrypter.return_value = "123"
            mock_decoder.return_value = user_claims

            response = await oauth2_callback(
                mock_request,
                code=mock_code,
                registry_oauth2_code_verifier="a-cookie",
                user_service=mock_user_service,
            )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        assert response.headers["location"] == f"{mock_settings.registry_client_url}"

        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())

        assert cookies[mock_settings.session_cookie_name].value == "mock-access-token"
        assert cookies[mock_settings.refresh_cookie_name].value == "mock-refresh-token"
        assert cookies[mock_settings.csrf_cookie_name].value == compute_csrf_token("mock-access-token")

    @pytest.mark.asyncio
    async def test_oauth2_callback_rejects_invalid_access_token_signature(self, mock_request, mock_settings, mock_code):
        """Reject auth-server access tokens that fail signature verification."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "tampered-access-token"}

        mock_user_service = Mock()
        mock_user_service.create_user = AsyncMock()
        mock_user_service.get_user_by_user_id = AsyncMock()

        with (
            patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client,
            patch("registry.api.redirect_routes.decrypt_value") as mock_decrypter,
            patch("registry.api.redirect_routes.decode_jwt") as mock_decoder,
        ):
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_decrypter.return_value = "123"
            mock_decoder.side_effect = InvalidSignatureError("bad signature")

            response = await oauth2_callback(
                mock_request,
                code=mock_code,
                registry_oauth2_code_verifier="a-cookie",
                user_service=mock_user_service,
            )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        assert "oauth2_exchange_error" in response.headers["location"]
        mock_decoder.assert_called_once_with(
            "tampered-access-token",
            mock_settings.jwt_public_key,
            mock_settings.jwt_issuer,
        )
        mock_user_service.create_user.assert_not_called()
        mock_user_service.get_user_by_user_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth2_callback_user_not_found(self, mock_request, mock_code, mock_settings):
        """Test OAuth2 callback when user is not found in DB."""
        # Mock httpx AsyncClient to return a token without user_id
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "test-access-token-no-user-id"}

        # User claims without user_id to trigger create_user
        user_claims = {
            "sub": "testuser",
            "email": "test@test.com",
            "name": "Test User",
            "groups": [],
            "provider": "local",
        }

        mock_user = Mock()
        mock_user.id = ObjectId("507f1f77bcf86cd799439013")

        mock_user_service = Mock()
        with (
            patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client,
            patch("registry.api.redirect_routes.decode_jwt") as mock_decoder,
            patch("registry.api.redirect_routes.decrypt_value") as mock_decrypter,
        ):
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_decoder.return_value = user_claims
            mock_decrypter.return_value = "123"

            # Mock create_user to return a new user
            mock_user_service.create_user = AsyncMock(return_value=mock_user)
            mock_user_service.get_user_by_user_id = AsyncMock(return_value=mock_user)

            response = await oauth2_callback(
                mock_request,
                code=mock_code,
                registry_oauth2_code_verifier="a-cookie",
                user_service=mock_user_service,
            )

            assert isinstance(response, RedirectResponse)
            assert response.status_code == 302
            mock_user_service.create_user.assert_called_once()

    @pytest.mark.asyncio
    async def test_oauth2_callback_with_error(self, mock_request):
        """Test OAuth2 callback with error parameter."""
        response = await oauth2_callback(
            mock_request,
            error="oauth2_error",
            details="Provider error",
            user_service=Mock(),
        )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        assert "error=" in response.headers["location"]
        assert "OAuth2%20provider%20error" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_oauth2_callback_oauth2_init_failed(self, mock_request):
        """Test OAuth2 callback with init failed error."""
        response = await oauth2_callback(
            mock_request,
            error="oauth2_init_failed",
            user_service=Mock(),
        )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        assert "Failed%20to%20initiate%20OAuth2%20login" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_oauth2_callback_oauth2_callback_failed(self, mock_request):
        """Test OAuth2 callback with callback failed error."""
        response = await oauth2_callback(
            mock_request,
            error="oauth2_callback_failed",
            user_service=Mock(),
        )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        assert "OAuth2%20authentication%20failed" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_oauth2_callback_general_exception(self, mock_request, mock_code):
        """Test OAuth2 callback with general exception."""
        with patch("registry.api.redirect_routes.logger"):
            # Mock httpx to return a failed response (non-200)
            mock_response = Mock()
            mock_response.status_code = 500

            with (
                patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client,
                patch("registry.api.redirect_routes.decode_jwt") as mock_decoder,
                patch("registry.api.redirect_routes.decrypt_value") as mock_decrypter,
            ):
                mock_client_instance = mock_client.return_value.__aenter__.return_value
                mock_client_instance.post = AsyncMock(return_value=mock_response)
                mock_decoder.return_value = {}
                mock_decrypter.return_value = "123"

                response = await oauth2_callback(
                    mock_request,
                    code=mock_code,
                    user_service=Mock(),
                )

                assert isinstance(response, RedirectResponse)
                assert response.status_code == 302
                # When status_code != 200, it returns oauth2_token_exchange_failed
                assert "oauth2_token_exchange_failed" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_logout_post_clears_csrf_cookie(self, mock_settings):
        """Logout must clear the CSRF cookie alongside session and refresh cookies."""
        response = await logout_post()

        assert response.status_code == 204

        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())

        assert mock_settings.csrf_cookie_name in cookies
        assert cookies[mock_settings.csrf_cookie_name].value == ""
        assert cookies[mock_settings.csrf_cookie_name]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_refresh_token_success_sets_csrf_cookie(self, mock_request, mock_settings):
        """Successful token refresh must issue new access, CSRF, and rotated refresh cookies."""

        claims = {
            "user_id": "123",
            "sub": "testuser",
            "auth_method": "oauth2",
            "provider": "entra",
            "groups": ["admin"],
            "scope": "read write",
            "role": "user",
            "email": "test@example.com",
            "session_started_at": int(time.time()) - 3600,  # 1 hour ago — well within 14-day cap
        }

        with (
            patch("registry.api.redirect_routes.verify_refresh_token", return_value=claims),
            patch("registry.api.redirect_routes.generate_access_token", return_value="new-access-token"),
            patch("registry.api.redirect_routes.generate_refresh_token", return_value="new-refresh-token"),
        ):
            response = await refresh_token(mock_request, refresh="valid-refresh-token", is_https=False)

        assert response.status_code == 200

        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())

        assert cookies[mock_settings.session_cookie_name].value == "new-access-token"
        assert cookies[mock_settings.csrf_cookie_name].value == compute_csrf_token("new-access-token")
        assert cookies[mock_settings.refresh_cookie_name].value == "new-refresh-token"
        assert cookies[mock_settings.refresh_cookie_name]["max-age"] == "172800"

    @pytest.mark.asyncio
    async def test_refresh_token_no_refresh_cookie_clears_csrf_cookie(self, mock_request, mock_settings):
        """No refresh cookie must clear the CSRF cookie and return 401."""
        response = await refresh_token(mock_request, refresh=None, is_https=False)

        assert response.status_code == 401

        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())

        assert cookies[mock_settings.csrf_cookie_name].value == ""
        assert cookies[mock_settings.csrf_cookie_name]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_refresh_token_invalid_token_clears_csrf_cookie(self, mock_request, mock_settings):
        """An invalid or expired refresh token must clear the CSRF cookie and return 401."""
        with patch("registry.api.redirect_routes.verify_refresh_token", return_value=None):
            response = await refresh_token(mock_request, refresh="expired-token", is_https=False)

        assert response.status_code == 401

        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())

        assert cookies[mock_settings.csrf_cookie_name].value == ""
        assert cookies[mock_settings.csrf_cookie_name]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_refresh_token_missing_scopes_clears_csrf_cookie(self, mock_request, mock_settings):
        """A refresh token with no resolvable scopes must clear the CSRF cookie and return 401."""
        claims = {
            "user_id": "123",
            "sub": "testuser",
            "auth_method": "oauth2",
            "provider": "entra",
            "groups": [],
            "scope": "",
            "role": "user",
            "email": "test@example.com",
        }

        with patch("registry.api.redirect_routes.verify_refresh_token", return_value=claims):
            response = await refresh_token(mock_request, refresh="valid-refresh-token", is_https=False)

        assert response.status_code == 401

        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())

        assert cookies[mock_settings.csrf_cookie_name].value == ""
        assert cookies[mock_settings.csrf_cookie_name]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_refresh_token_rotated_claims_match_prior_token(self, mock_request, mock_settings):
        """Rotated refresh token must carry forward the same claims from the prior token."""
        import time

        fixed_session_start = int(time.time()) - 3600  # 1 hour ago — well within 14-day cap
        claims = {
            "user_id": "123",
            "sub": "testuser",
            "auth_method": "oauth2",
            "provider": "entra",
            "groups": ["jarvis-registry-admin"],
            "scope": "servers-read servers-write",
            "role": "admin",
            "email": "admin@example.com",
            "session_started_at": fixed_session_start,
        }

        with (
            patch("registry.api.redirect_routes.verify_refresh_token", return_value=claims),
            patch("registry.api.redirect_routes.generate_access_token", return_value="new-access-token"),
            patch(
                "registry.api.redirect_routes.generate_refresh_token", return_value="new-refresh-token"
            ) as mock_gen_refresh,
        ):
            await refresh_token(mock_request, refresh="valid-refresh-token", is_https=False)

        mock_gen_refresh.assert_called_once()
        call_kwargs = mock_gen_refresh.call_args.kwargs
        assert call_kwargs["user_id"] == "123"
        assert call_kwargs["username"] == "testuser"
        assert call_kwargs["groups"] == ["jarvis-registry-admin"]
        assert call_kwargs["role"] == "admin"
        assert call_kwargs["email"] == "admin@example.com"
        assert call_kwargs["session_started_at"] == fixed_session_start

    @pytest.mark.asyncio
    async def test_refresh_token_absolute_session_cap_exceeded_returns_401(self, mock_request, mock_settings):
        """A refresh where now - session_started_at > 14 days must return 401 and clear all cookies."""
        import time

        old_start = int(time.time()) - (15 * 86400)  # 15 days ago
        claims = {
            "user_id": "123",
            "sub": "testuser",
            "auth_method": "oauth2",
            "provider": "entra",
            "groups": ["admin"],
            "scope": "read write",
            "role": "user",
            "email": "test@example.com",
            "session_started_at": old_start,
        }

        with (
            patch("registry.api.redirect_routes.verify_refresh_token", return_value=claims),
            patch("registry.api.redirect_routes.generate_access_token") as mock_gen_access,
            patch("registry.api.redirect_routes.generate_refresh_token") as mock_gen_refresh,
        ):
            response = await refresh_token(mock_request, refresh="valid-refresh-token", is_https=False)

        assert response.status_code == 401
        mock_gen_access.assert_not_called()
        mock_gen_refresh.assert_not_called()

        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())

        assert cookies[mock_settings.session_cookie_name]["max-age"] == "0"
        assert cookies[mock_settings.refresh_cookie_name]["max-age"] == "0"
        assert cookies[mock_settings.csrf_cookie_name]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_refresh_token_missing_session_started_at_grandfathered(self, mock_request, mock_settings):
        """A pre-deploy refresh token with no session_started_at must NOT be rejected."""
        claims = {
            "user_id": "123",
            "sub": "testuser",
            "auth_method": "oauth2",
            "provider": "entra",
            "groups": ["admin"],
            "scope": "read write",
            "role": "user",
            "email": "test@example.com",
            # No session_started_at — simulates a pre-deploy token
        }

        with (
            patch("registry.api.redirect_routes.verify_refresh_token", return_value=claims),
            patch("registry.api.redirect_routes.generate_access_token", return_value="new-access-token"),
            patch(
                "registry.api.redirect_routes.generate_refresh_token", return_value="new-refresh-token"
            ) as mock_gen_refresh,
        ):
            response = await refresh_token(mock_request, refresh="valid-refresh-token", is_https=False)

        assert response.status_code == 200

        # The newly-rotated refresh token must receive a fresh session_started_at (approx now)
        import time

        call_kwargs = mock_gen_refresh.call_args.kwargs
        assert "session_started_at" in call_kwargs
        assert call_kwargs["session_started_at"] is not None
        assert abs(call_kwargs["session_started_at"] - int(time.time())) <= 5

    @pytest.mark.asyncio
    async def test_refresh_token_401_branches_do_not_set_refresh_cookie(self, mock_request, mock_settings):
        """All 401 error branches (no cookie, invalid token, no scopes) must not set a refresh cookie."""
        refresh_cookie_name = mock_settings.refresh_cookie_name

        # Branch 1: no cookie
        response = await refresh_token(mock_request, refresh=None, is_https=False)
        assert response.status_code == 401
        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())
        if refresh_cookie_name in cookies:
            assert cookies[refresh_cookie_name]["max-age"] == "0"

        # Branch 2: invalid token
        with patch("registry.api.redirect_routes.verify_refresh_token", return_value=None):
            response = await refresh_token(mock_request, refresh="bad-token", is_https=False)
        assert response.status_code == 401
        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())
        if refresh_cookie_name in cookies:
            assert cookies[refresh_cookie_name]["max-age"] == "0"

        # Branch 3: no scopes
        no_scope_claims = {
            "user_id": "123",
            "sub": "testuser",
            "auth_method": "oauth2",
            "provider": "entra",
            "groups": [],
            "scope": "",
            "role": "user",
            "email": "test@example.com",
        }
        with patch("registry.api.redirect_routes.verify_refresh_token", return_value=no_scope_claims):
            response = await refresh_token(mock_request, refresh="valid-token", is_https=False)
        assert response.status_code == 401
        cookies = SimpleCookie()
        for key, value in response.raw_headers:
            if key == b"set-cookie":
                cookies.load(value.decode())
        if refresh_cookie_name in cookies:
            assert cookies[refresh_cookie_name]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_oauth2_callback_filters_groups_before_minting_tokens(self, mock_request, mock_settings, mock_code):
        """oauth2_callback must pass only known groups to generate_token_pair."""
        mock_user = Mock()
        mock_user.id = "12345"
        mock_user.username = "testuser"
        mock_user.email = "test@example.com"
        mock_user.role = "user"
        mock_user.idp_id = "12345-6789"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "test-access-token"}
        mock_user_service = Mock()
        mock_user_service.get_user_by_user_id = AsyncMock(return_value=mock_user)

        user_claims = {
            "sub": "someone",
            "user_id": "12345",
            "groups": ["jarvis-registry-admin", "Some-Unrelated-Distro-List", "Teams-Channel-Foo"],
        }

        with (
            patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client,
            patch("registry.api.redirect_routes.decrypt_value", return_value="verifier"),
            patch("registry.api.redirect_routes.decode_jwt", return_value=user_claims),
            patch(
                "registry.api.redirect_routes.generate_token_pair",
                return_value=("mock-access-token", "mock-refresh-token"),
            ) as mock_gen_pair,
            patch(
                "registry.api.redirect_routes.filter_known_groups",
                return_value=["jarvis-registry-admin"],
            ) as mock_filter,
        ):
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            await oauth2_callback(
                mock_request,
                code=mock_code,
                registry_oauth2_code_verifier="a-cookie",
                user_service=mock_user_service,
            )

        mock_filter.assert_called_once()
        filter_input = mock_filter.call_args.args[0]
        assert "Some-Unrelated-Distro-List" in filter_input
        assert "Teams-Channel-Foo" in filter_input

        passed_user_info = mock_gen_pair.call_args.kwargs.get("user_info") or mock_gen_pair.call_args.args[0]
        assert passed_user_info["groups"] == ["jarvis-registry-admin"]
