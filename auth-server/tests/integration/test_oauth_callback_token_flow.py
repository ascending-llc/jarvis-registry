"""
Integration tests for OAuth callback flow with standard token exchange.

Tests the refactored oauth2_callback that always uses OAuth client flow
and registry redirect endpoint that calls /oauth2/token to decode JWT.
"""

import base64
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from auth_server.core.state import authorization_codes_storage
from auth_server.deps import get_auth_provider, get_oauth2_config, get_signer, get_user_service
from auth_server.server import app

API_PREFIX = "/auth"


@pytest.fixture
def mock_user_service():
    """Mock user_service for user_id resolution."""
    mock_service = MagicMock()

    # Default behavior: resolve_user_id returns a valid user_id
    mock_service.resolve_user_id = AsyncMock(return_value="507f1f77bcf86cd799439011")

    yield mock_service


@pytest.mark.integration
@pytest.mark.oauth_callback
class TestOAuth2CallbackStandardFlow:
    """Test OAuth2 callback with standard OAuth client flow."""

    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    @patch("auth_server.routes.oauth_flow.get_user_info")
    def test_oauth_callback_always_generates_authorization_code(
        self,
        mock_get_user_info,
        mock_exchange_token,
        clear_device_storage,
        mock_user_service,
    ):
        """Test that oauth2_callback always generates authorization code (for both external clients and registry)."""
        # Mock provider token exchange
        mock_exchange_token.return_value = {"access_token": "provider_access_token", "id_token": "provider_id_token"}

        # Mock user info from provider
        mock_get_user_info.return_value = {
            "sub": "provider-user-123",
            "preferred_username": "testuser",
            "email": "test@example.com",
            "name": "Test User",
            "groups": ["user-group"],
        }

        oauth2_config = {
            "providers": {
                "keycloak": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://keycloak/token",
                    "user_info_url": "http://keycloak/userinfo",
                    "username_claim": "preferred_username",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": "groups",
                }
            }
        }

        # Mock settings
        with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.registry_app_name = "registry-internal-client"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}

            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_user_service] = lambda: mock_user_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: MagicMock()

            test_client = TestClient(app)

            # Create a valid session cookie
            session_data = {
                "state": "test-state-123",
                "client_state": None,
                "provider": "keycloak",
                "redirect_uri": "http://localhost:3000/redirect",
                "client_id": "mock-client-id",
                "code_challenge": "123",
                "code_challenge_method": "S256",
                "client_redirect_uri": "http://localhost:3000/redirect",
            }
            temp_session = test_signer.dumps(session_data)

            # Make callback request
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider_auth_code", "state": "test-state-123"},
                cookies={"oauth2_temp_session": temp_session},
                follow_redirects=False,
            )

            print(response.text)

            # Should redirect with authorization code
            assert response.status_code == 302
            location = response.headers["location"]
            assert "code=" in location
            assert location.startswith("http://localhost:3000/redirect")

            # Extract authorization code
            import urllib.parse

            parsed_url = urllib.parse.urlparse(location)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            auth_code = query_params.get("code", [None])[0]
            assert auth_code is not None

            # Verify code is stored in authorization_codes_storage
            assert auth_code in authorization_codes_storage
            code_data = authorization_codes_storage[auth_code]

            # Verify standard OAuth client flow structure
            assert code_data["client_id"] == "mock-client-id"
            assert code_data["redirect_uri"] == "http://localhost:3000/redirect"
            assert "user_info" in code_data
            assert code_data["user_info"]["username"] == "testuser"
            assert code_data["user_info"]["user_id"] == "507f1f77bcf86cd799439011"
            assert code_data["used"] is False

    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    @patch("auth_server.routes.oauth_flow.get_user_info")
    def test_oauth_callback_external_client_with_client_id(
        self,
        mock_get_user_info,
        mock_exchange_token,
        clear_device_storage,
        mock_user_service,
    ):
        """Test oauth2_callback with explicit client_id (external OAuth client)."""
        mock_exchange_token.return_value = {"access_token": "provider_access_token", "id_token": "provider_id_token"}

        mock_get_user_info.return_value = {
            "sub": "provider-user-456",
            "preferred_username": "externaluser",
            "email": "external@example.com",
            "name": "External User",
            "groups": ["external-group"],
        }

        oauth2_config = {
            "providers": {
                "keycloak": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://keycloak/token",
                    "user_info_url": "http://keycloak/userinfo",
                    "username_claim": "preferred_username",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": "groups",
                }
            }
        }

        with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}

            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_user_service] = lambda: mock_user_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: MagicMock()

            test_client = TestClient(app)

            # Session with explicit client_id
            session_data = {
                "state": "test-state-456",
                "client_state": "external-state-abc",
                "provider": "keycloak",
                "redirect_uri": "http://external-app.com/callback",
                "client_id": "external-client-123",
                "client_redirect_uri": "http://external-app.com/callback",
                "code_challenge": "test-challenge",
                "code_challenge_method": "S256",
            }
            temp_session = test_signer.dumps(session_data)

            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider_auth_code", "state": "test-state-456"},
                cookies={"oauth2_temp_session": temp_session},
                follow_redirects=False,
            )

            # Should redirect to external client with state
            assert response.status_code == 302
            location = response.headers["location"]
            assert location.startswith("http://external-app.com/callback")
            assert "code=" in location
            assert "state=external-state-abc" in location

            # Extract and verify authorization code
            import urllib.parse

            parsed_url = urllib.parse.urlparse(location)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            auth_code = query_params.get("code", [None])[0]

            code_data = authorization_codes_storage[auth_code]
            assert code_data["client_id"] == "external-client-123"
            assert code_data["redirect_uri"] == "http://external-app.com/callback"
            assert code_data["code_challenge"] == "test-challenge"
            assert code_data["code_challenge_method"] == "S256"

    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    @patch("auth_server.routes.oauth_flow.decode_jwt_unverified")
    def test_oauth_callback_keycloak_id_token_parsing(
        self, mock_jwt_decode, mock_exchange_token, clear_device_storage, mock_user_service
    ):
        """Test that Keycloak ID token is properly parsed."""
        mock_exchange_token.return_value = {"access_token": "keycloak_access", "id_token": "keycloak_id_token"}

        # Mock JWT decode for ID token
        mock_jwt_decode.return_value = {
            "sub": "keycloak-sub-789",
            "preferred_username": "keycloakuser",
            "email": "keycloak@example.com",
            "name": "Keycloak User",
            "groups": ["/admin", "/users"],
        }

        oauth2_config = {
            "providers": {
                "keycloak": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://keycloak/token",
                    "user_info_url": "http://keycloak/userinfo",
                }
            }
        }

        with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.registry_app_name = "registry-internal-client"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}

            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_user_service] = lambda: mock_user_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: MagicMock()

            test_client = TestClient(app)

            session_data = {
                "state": "test-state-789",
                "client_state": None,
                "provider": "keycloak",
                "redirect_uri": "http://localhost:3000/redirect",
                "client_id": "mock-client-id",
                "code_challenge": "123",
                "code_challenge_method": "S256",
                "client_redirect_uri": "http://localhost:3000/redirect",
            }
            temp_session = test_signer.dumps(session_data)

            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "keycloak_code", "state": "test-state-789"},
                cookies={"oauth2_temp_session": temp_session},
                follow_redirects=False,
            )

            # Extract authorization code
            import urllib.parse

            location = response.headers["location"]
            parsed_url = urllib.parse.urlparse(location)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            auth_code = query_params.get("code", [None])[0]

            # Verify user info from ID token
            code_data = authorization_codes_storage[auth_code]
            user_info = code_data["user_info"]
            assert user_info["username"] == "keycloakuser"
            assert user_info["email"] == "keycloak@example.com"
            assert user_info["groups"] == ["/admin", "/users"]
            assert user_info["idp_id"] == "keycloak-sub-789"

    def test_oauth_callback_user_id_not_resolved(self, clear_device_storage):
        """Test oauth2_callback when user_id cannot be resolved (user not in MongoDB)."""
        with patch("auth_server.routes.oauth_flow.exchange_code_for_token") as mock_exchange:
            with patch("auth_server.routes.oauth_flow.get_user_info") as mock_get_user:
                mock_exchange.return_value = {"access_token": "token", "id_token": "id"}
                mock_get_user.return_value = {
                    "sub": "new-user",
                    "preferred_username": "newuser",
                    "email": "new@example.com",
                    "name": "New User",
                    "groups": [],
                }

                oauth2_config = {
                    "providers": {
                        "keycloak": {
                            "enabled": True,
                            "client_id": "test",
                            "client_secret": "secret",
                            "token_url": "http://keycloak/token",
                            "user_info_url": "http://keycloak/userinfo",
                            "username_claim": "preferred_username",
                            "email_claim": "email",
                            "name_claim": "name",
                            "groups_claim": "groups",
                        }
                    }
                }

                with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
                    mock_settings.registry_url = "http://localhost:3000"
                    mock_settings.registry_app_name = "registry-internal-client"
                    mock_settings.auth_server_external_url = "http://localhost:8888"
                    mock_settings.auth_server_url = "http://localhost:8888"
                    mock_settings.oauth_session_ttl_seconds = 600
                    mock_settings.secret_key = "test-secret-key"

                    test_signer = URLSafeTimedSerializer("test-secret-key")

                    app.dependency_overrides = {}

                    mock_user_service = MagicMock()
                    mock_user_service.resolve_user_id = AsyncMock(return_value=None)

                    app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
                    app.dependency_overrides[get_user_service] = lambda: mock_user_service
                    app.dependency_overrides[get_signer] = lambda: test_signer
                    app.dependency_overrides[get_auth_provider] = lambda: MagicMock()

                    test_client = TestClient(app)

                    session_data = {
                        "state": "test-state",
                        "client_state": None,
                        "provider": "keycloak",
                        "redirect_uri": "http://localhost:3000/redirect",
                        "client_id": "mock-client-id",
                        "code_challenge": "123",
                        "code_challenge_method": "S256",
                        "client_redirect_uri": "http://localhost:3000/redirect",
                    }
                    temp_session = test_signer.dumps(session_data)

                    response = test_client.get(
                        f"{API_PREFIX}/oauth2/callback/keycloak",
                        params={"code": "code", "state": "test-state"},
                        cookies={"oauth2_temp_session": temp_session},
                        follow_redirects=False,
                    )

                    # Still should generate authorization code (user_id is None)
                    assert response.status_code == 302
                    location = response.headers["location"]

                    import urllib.parse

                    parsed_url = urllib.parse.urlparse(location)
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    auth_code = query_params.get("code", [None])[0]

                    code_data = authorization_codes_storage[auth_code]
                    # user_id will be None when not found
                    assert "user_info" in code_data


@pytest.mark.integration
@pytest.mark.oauth_token
class TestOAuth2TokenEndpoint:
    """Test /oauth2/token endpoint with authorization code grant."""

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_with_authorization_code(self, clear_device_storage, mock_user_service, content_type):
        """Test token endpoint exchanges authorization code for JWT with user_id."""
        # Create authorization code directly
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {
                "user_id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "groups": ["user-group"],
                "idp_id": "provider-sub-123",
            },
            "client_id": "test-client",
            "expires_at": current_time + 600,
            "used": False,
            "redirect_uri": "http://localhost/callback",
            "resolved_scope": ["servers-read", "agents-read"],
            "resource": None,
            "created_at": current_time,
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": "test-client",
            "redirect_uri": "http://localhost/callback",
        }

        # Exchange code for token
        with patch("auth_server.routes.oauth_flow.mint_managed_agent_token") as mock_mint_token:
            mock_mint_token.return_value = "mock-jwt-token-with-user-id"

            app.dependency_overrides = {}

            app.dependency_overrides[get_user_service] = lambda: mock_user_service

            test_client = TestClient(app)

            kwargs = {"json": payload} if content_type == "json" else {"data": payload}
            response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

            assert response.status_code == 200
            token_data = response.json()

            assert token_data["access_token"] == "mock-jwt-token-with-user-id"
            assert token_data["token_type"] == "Bearer"
            assert "expires_in" in token_data
            assert "refresh_token" in token_data

            # Verify managed-agent token was minted with resolved user identity
            assert mock_mint_token.call_args.kwargs["subject"] == "testuser"
            assert mock_mint_token.call_args.kwargs["client_id"] == "test-client"
            token_claims = mock_mint_token.call_args.kwargs["extra_claims"]
            assert token_claims["user_id"] == "507f1f77bcf86cd799439011"
            assert token_claims["groups"] == ["user-group"]
            assert token_claims["scope"] == "servers-read agents-read"
            assert token_claims["token_use"] == "access"
            assert token_claims["auth_provider"] is not None

            # Code should be deleted after successful exchange
            assert auth_code not in authorization_codes_storage

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_quick_suite_basic_auth_fallback(
        self,
        clear_device_storage,
        mock_user_service,
        content_type,
    ):
        """Test Quick Suite compatibility: resolve client_id/client_secret from Basic auth when body omits them."""
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {
                "user_id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "groups": ["user-group"],
                "idp_id": "provider-sub-123",
            },
            "client_id": "test-client",
            "expires_at": current_time + 600,
            "used": False,
            "redirect_uri": "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback",
            "resource": None,
            "created_at": current_time,
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": "test-code-verifier",
            "redirect_uri": "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback",
        }

        basic_auth = base64.b64encode(b"test-client:test-secret").decode("ascii")

        with patch("auth_server.routes.oauth_flow.mint_managed_agent_token") as mock_mint_token:
            mock_mint_token.return_value = "mock-jwt-token-with-user-id"

            app.dependency_overrides = {}
            app.dependency_overrides[get_user_service] = lambda: mock_user_service

            test_client = TestClient(app)

            headers = {"Authorization": f"Basic {basic_auth}"}
            kwargs = (
                {"json": payload, "headers": headers}
                if content_type == "json"
                else {"data": payload, "headers": headers}
            )
            response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

            assert response.status_code == 200

            token_data = response.json()
            assert token_data["access_token"] == "mock-jwt-token-with-user-id"

            assert mock_mint_token.call_args.kwargs["client_id"] == "test-client"
            token_claims = mock_mint_token.call_args.kwargs["extra_claims"]
            assert token_claims["user_id"] == "507f1f77bcf86cd799439011"
            assert token_claims["token_use"] == "access"

            assert auth_code not in authorization_codes_storage

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_non_quick_suite_basic_auth_no_fallback(
        self,
        clear_device_storage,
        mock_user_service,
        content_type,
    ):
        """Test non-Quick Suite clients using Basic auth do not get client_id fallback."""
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {
                "user_id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "groups": ["user-group"],
                "idp_id": "provider-sub-123",
            },
            "client_id": "test-client",
            "expires_at": current_time + 600,
            "used": False,
            "redirect_uri": "https://example.com/oauth/callback",
            "resource": None,
            "created_at": current_time,
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": "test-code-verifier",
            "redirect_uri": "https://example.com/oauth/callback",
        }

        basic_auth = base64.b64encode(b"test-client:test-secret").decode("ascii")

        with patch("auth_server.routes.oauth_flow.mint_managed_agent_token") as mock_mint_token:
            app.dependency_overrides = {}
            app.dependency_overrides[get_user_service] = lambda: mock_user_service

            test_client = TestClient(app)

            headers = {"Authorization": f"Basic {basic_auth}"}
            kwargs = (
                {"json": payload, "headers": headers}
                if content_type == "json"
                else {"data": payload, "headers": headers}
            )
            response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

            # Should reject since client_id fallback only applies to Quick Suite hosts
            assert response.status_code == 400
            assert response.json()["error"] == "invalid_request"
            assert response.json()["error_description"] == "client_id is required"
            mock_mint_token.assert_not_called()

            # Request should fail before code exchange, so code remains stored.
            assert auth_code in authorization_codes_storage

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_code_already_used(self, test_client, clear_device_storage, content_type):
        """Test token endpoint rejects already-used authorization code."""
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {"username": "testuser"},
            "client_id": "test-client",
            "expires_at": current_time + 600,
            "used": True,  # Already used
            "redirect_uri": "http://localhost/callback",
            "created_at": current_time,
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": "test-client",
            "redirect_uri": "http://localhost/callback",
        }

        app.dependency_overrides = {}

        app.dependency_overrides[get_user_service] = lambda: mock_user_service

        test_client = TestClient(app)

        kwargs = {"json": payload} if content_type == "json" else {"data": payload}
        response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"
        assert "already used" in response.json()["error_description"]

        # Code should be deleted
        assert auth_code not in authorization_codes_storage

    def test_token_endpoint_unsupported_content_type(self):
        """Test token endpoint rejects unsupported content-type with 415."""
        app.dependency_overrides = {}

        app.dependency_overrides[get_user_service] = lambda: mock_user_service

        test_client = TestClient(app)

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            content="grant_type=authorization_code&client_id=test-client",
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 415
