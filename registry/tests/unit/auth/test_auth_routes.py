"""
Unit tests for authentication routes.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from bson import ObjectId
from fastapi import Request
from fastapi.responses import RedirectResponse

from registry.api.redirect_routes import get_oauth2_providers, oauth2_callback, oauth2_login_redirect


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
            mock_settings.session_max_age_seconds = 3600
            mock_settings.templates_dir = "/templates"
            mock_settings.registry_client_url = "http://localhost:8000/"
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
    async def test_oauth2_login_redirect_success(self, mock_request, mock_settings):
        """Test successful OAuth2 login redirect."""
        provider = "google"

        response = await oauth2_login_redirect(provider, mock_request)

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        # The route uses auth_server_external_url for the redirect
        expected_url = f"{mock_settings.auth_server_external_url}/oauth2/login/{provider}?redirect_uri=http://localhost:8000/&state="
        # The state param is dynamic, so just check the prefix
        assert response.headers["location"].startswith(expected_url)

    @pytest.mark.asyncio
    async def test_oauth2_login_redirect_exception(self, mock_request, mock_settings):
        """Test OAuth2 login redirect with exception."""
        provider = "invalid"

        with patch("registry.api.redirect_routes.logger"):
            # Force an exception by making str() fail
            mock_request.base_url = Mock()
            mock_request.base_url.__str__ = Mock(side_effect=Exception("URL error"))

            response = await oauth2_login_redirect(provider, mock_request)

            assert isinstance(response, RedirectResponse)
            assert response.status_code == 302
            assert "/login?error=oauth2_redirect_failed" in response.headers["location"]

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

        with (
            patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client,
            patch(
                "jwt.decode",
                return_value={
                    "user_id": mock_user.id,
                    "sub": mock_user.username,
                    "email": mock_user.email,
                    "name": "Test User",
                    "groups": [],
                    "provider": "keycloak",
                },
            ),
            patch(
                "registry.api.redirect_routes.generate_token_pair",
                return_value=("mock-access-token", "mock-refresh-token"),
            ),
        ):
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.post = AsyncMock(return_value=mock_response)

            response = await oauth2_callback(
                mock_request,
                code=mock_code,
                user_service=mock_user_service,
            )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        # Registry client URL may or may not have trailing slash
        assert response.headers["location"].rstrip("/") == "http://localhost:8000"

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
            patch("jwt.decode", return_value=user_claims),
        ):
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.post = AsyncMock(return_value=mock_response)

            # Mock create_user to return a new user
            mock_user_service.create_user = AsyncMock(return_value=mock_user)
            mock_user_service.get_user_by_user_id = AsyncMock(return_value=mock_user)

            response = await oauth2_callback(
                mock_request,
                code=mock_code,
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

            with patch("registry.api.redirect_routes.httpx.AsyncClient") as mock_client:
                mock_client_instance = mock_client.return_value.__aenter__.return_value
                mock_client_instance.post = AsyncMock(return_value=mock_response)

                response = await oauth2_callback(
                    mock_request,
                    code=mock_code,
                    user_service=Mock(),
                )

                assert isinstance(response, RedirectResponse)
                assert response.status_code == 302
                # When status_code != 200, it returns oauth2_token_exchange_failed
                assert "oauth2_token_exchange_failed" in response.headers["location"]
