"""
Integration tests for OAuth 2.0 Access Token Scoping and Refresh Token Rotation.

Tests:
- Access token scoping during authorization code flow
- Refresh token rotation (OAuth 2.1 best practice)
"""

import secrets
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi.testclient import TestClient

from auth_server.deps import get_auth_provider, get_oauth2_config, get_oauth_state_store, get_signer, get_user_service
from auth_server.server import app
from registry_pkgs.core.jwt_utils import decode_jwt_unverified
from registry_pkgs.core.oauth_state_store import REFRESH_TOKEN_TTL_SECONDS as REFRESH_TOKEN_EXPIRY_SECONDS
from tests.conftest import test_consent_store
from tests.integration.conftest import _mock_keycloak_provider
from tests.support.oauth_state_store import authorization_codes_storage, refresh_tokens_storage, test_oauth_state_store

# API prefix for OAuth endpoints (set in conftest.py via AUTH_SERVER_API_PREFIX env var)
API_PREFIX = "/auth"

# PKCE constants for testing
TEST_CODE_VERIFIER = "test-verifier-1234567890123456789012345678901234567890"  # At least 43 chars
TEST_CODE_CHALLENGE = create_s256_code_challenge(TEST_CODE_VERIFIER)


@pytest.fixture
def mock_user_service():
    """Mock user_service for user_id resolution."""
    mock_service = MagicMock()
    mock_service.resolve_user_id = AsyncMock(return_value="user-id-123")
    return mock_service


@pytest.fixture
def test_client_with_user_service(mock_user_service):
    """Create test client with user_service dependency override."""
    app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
    app.dependency_overrides[get_user_service] = lambda: mock_user_service
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.oauth_flow
class TestAccessTokenScoping:
    """Integration tests for access token scope negotiation in authorization code flow."""

    def _create_mock_callback_state(self, client_id: str, redirect_uri: str, requested_scope: str | None = None):
        """Helper to create authorization code with proper session data."""
        authorization_code = secrets.token_urlsafe(32)
        current_time = int(time.time())

        # Mock user info with groups that map to scopes
        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "name": "Test User",
            "idp_id": "test-idp-id",
            "groups": ["jarvis-registry-admin"],  # Maps to all scopes in scopes.yml
        }

        authorization_codes_storage[authorization_code] = {
            "token_data": {"access_token": "mock-idp-token", "id_token": "mock-id-token"},
            "user_info": user_info,
            "client_id": client_id,
            "expires_at": current_time + 600,
            "used": False,
            "code_challenge": TEST_CODE_CHALLENGE,
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
            "resource": None,
            "created_at": current_time,
            "resolved_scope": None,  # Will be computed based on requested_scope
        }

        return authorization_code, user_info

    @patch("auth_server.routes.oauth_flow.map_groups_to_scopes")
    def test_no_scope_requested_uses_default_user_scopes(
        self, mock_map_groups, test_client_with_user_service: TestClient, clear_device_storage
    ):
        """Test that when no scope is requested, user receives their default scopes."""
        # Setup mocks
        default_scopes = ["servers-read", "agents-read", "servers-write"]
        mock_map_groups.return_value = default_scopes

        # Create authorization code without resolved_scope (backward compatibility)
        client_id = "test-client"
        redirect_uri = "https://example.com/callback"
        auth_code, user_info = self._create_mock_callback_state(client_id, redirect_uri, requested_scope=None)

        # Token request
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": TEST_CODE_VERIFIER,
            },
        )

        assert response.status_code == 200
        token_data = response.json()

        # Decode JWT to verify scope
        access_token = token_data["access_token"]
        jwt_payload = decode_jwt_unverified(access_token)

        # Should contain all default user scopes
        assert jwt_payload["scope"] == " ".join(default_scopes)

    @patch("auth_server.routes.oauth_flow.map_groups_to_scopes")
    def test_requested_scope_subset_of_user_scopes(
        self, mock_map_groups, test_client_with_user_service: TestClient, clear_device_storage
    ):
        """Test that when client requests a subset of user scopes, intersection is returned."""
        # Setup mocks
        default_scopes = ["servers-read", "agents-read", "servers-write", "agents-write"]
        mock_map_groups.return_value = default_scopes

        # Simulate callback flow with requested scope
        client_id = "test-client"
        redirect_uri = "https://example.com/callback"
        requested_scopes = ["servers-read", "agents-read"]  # Subset

        # Create authorization code with resolved_scope (negotiated in callback)
        auth_code, user_info = self._create_mock_callback_state(client_id, redirect_uri)
        authorization_codes_storage[auth_code]["resolved_scope"] = requested_scopes

        # Token request
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": TEST_CODE_VERIFIER,
            },
        )

        assert response.status_code == 200
        token_data = response.json()

        # Decode JWT to verify scope
        access_token = token_data["access_token"]
        jwt_payload = decode_jwt_unverified(access_token)

        # Should contain only requested scopes (intersection)
        assert jwt_payload["scope"] == " ".join(requested_scopes)
        assert "servers-write" not in jwt_payload["scope"]
        assert "agents-write" not in jwt_payload["scope"]

    @patch("auth_server.routes.oauth_flow.get_token_kid")
    @patch("auth_server.routes.oauth_flow.decode_jwt_with_jwk")
    @patch("auth_server.routes.oauth_flow.map_groups_to_scopes")
    @patch("auth_server.routes.oauth_flow.get_user_info")
    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    def test_scope_negotiation_in_callback_success(
        self,
        mock_exchange,
        mock_get_user_info,
        mock_map_groups,
        mock_decode_jwt,
        mock_get_token_kid,
        clear_device_storage,
        mock_user_service,
    ):
        """Test scope negotiation by calling actual /oauth2/callback route with proper mocks."""
        from itsdangerous import URLSafeTimedSerializer

        # Setup: user has these default scopes
        default_scopes = ["servers-read", "agents-read", "servers-write"]
        mock_map_groups.return_value = default_scopes

        # Client requests only a subset
        requested_scopes_str = "servers-read agents-read"

        # Mock OAuth provider token exchange
        mock_exchange.return_value = {
            "access_token": "provider_access_token",
            "id_token": "provider_id_token",
        }
        mock_get_token_kid.return_value = "test-kid"

        # Mock JWT decode to return valid claims (prevents DecodeError)
        mock_decode_jwt.return_value = {
            "sub": "user123",
            "preferred_username": "testuser",
            "email": "test@example.com",
            "name": "Test User",
            "groups": ["jarvis-registry-admin"],
        }

        # Mock user info (fallback path, not used if JWT decode works)
        mock_get_user_info.return_value = {
            "sub": "user123",
            "preferred_username": "testuser",
            "email": "test@example.com",
            "name": "Test User",
            "groups": ["jarvis-registry-admin"],
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
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}
            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_user_service] = lambda: mock_user_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = _mock_keycloak_provider

            test_client = TestClient(app)

            # Create signed session with requested_scope
            session_data = {
                "state": "test_state",
                "client_state": "client_state_123",
                "provider": "keycloak",
                "redirect_uri": "https://example.com/callback",
                "client_id": "test-client",
                "client_redirect_uri": "https://example.com/callback",
                "code_challenge": TEST_CODE_CHALLENGE,
                "code_challenge_method": "S256",
                "requested_scope": requested_scopes_str,
            }
            oauth2_temp_session = test_signer.dumps(session_data)

            # Call the actual callback route
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "auth_code_123", "state": "test_state"},
                cookies={"oauth2_temp_session": oauth2_temp_session},
                follow_redirects=False,
            )

            # Should redirect with authorization code
            assert response.status_code == 302
            redirect_url = response.headers["location"]
            assert redirect_url.startswith("https://example.com/callback?code=")
            assert "state=client_state_123" in redirect_url

            # Extract authorization code from redirect
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(redirect_url)
            query_params = parse_qs(parsed.query)
            auth_code = query_params["code"][0]
            assert query_params["state"][0] == "client_state_123"

            # Verify resolved_scope is stored correctly in authorization_codes_storage
            assert auth_code in authorization_codes_storage
            auth_code_data = authorization_codes_storage[auth_code]
            resolved_scope = auth_code_data["resolved_scope"]

            # Should be intersection: requested ∩ default
            assert resolved_scope == ["servers-read", "agents-read"]
            assert "servers-write" not in resolved_scope

            # Cleanup
            app.dependency_overrides = {}

    @patch("auth_server.routes.oauth_flow.get_token_kid")
    @patch("auth_server.routes.oauth_flow.decode_jwt_with_jwk")
    @patch("auth_server.routes.oauth_flow.map_groups_to_scopes")
    @patch("auth_server.routes.oauth_flow.get_user_info")
    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    def test_scope_negotiation_empty_intersection_returns_error(
        self,
        mock_exchange,
        mock_get_user_info,
        mock_map_groups,
        mock_decode_jwt,
        mock_get_token_kid,
        clear_device_storage,
        mock_user_service,
    ):
        """Test that empty scope intersection redirects with invalid_scope error."""
        from itsdangerous import URLSafeTimedSerializer

        # User only has these scopes
        default_scopes = ["servers-read", "agents-read"]
        mock_map_groups.return_value = default_scopes

        # Client requests completely different scopes
        requested_scopes_str = "admin-access system-ops"

        # Mock OAuth provider token exchange
        mock_exchange.return_value = {
            "access_token": "provider_access_token",
            "id_token": "provider_id_token",
        }
        mock_get_token_kid.return_value = "test-kid"

        # Mock JWT decode to return valid claims (prevents DecodeError)
        mock_decode_jwt.return_value = {
            "sub": "user456",
            "preferred_username": "basicuser",
            "email": "basic@example.com",
            "name": "Basic User",
            "groups": ["basic-user"],
        }

        # Mock user info (fallback path)
        mock_get_user_info.return_value = {
            "sub": "user456",
            "preferred_username": "basicuser",
            "email": "basic@example.com",
            "name": "Basic User",
            "groups": ["basic-user"],
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
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}
            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_user_service] = lambda: mock_user_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = _mock_keycloak_provider

            test_client = TestClient(app)

            # Create signed session with requested_scope
            session_data = {
                "state": "test_state",
                "client_state": "client_state_456",
                "provider": "keycloak",
                "redirect_uri": "https://example.com/callback",
                "client_id": "test-client",
                "client_redirect_uri": "https://example.com/callback",
                "code_challenge": TEST_CODE_CHALLENGE,
                "code_challenge_method": "S256",
                "requested_scope": requested_scopes_str,
            }
            oauth2_temp_session = test_signer.dumps(session_data)

            # Call the actual callback route
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "auth_code_456", "state": "test_state"},
                cookies={"oauth2_temp_session": oauth2_temp_session},
                follow_redirects=False,
            )

            # Should redirect with error
            assert response.status_code == 302
            redirect_url = response.headers["location"]

            # Verify redirect contains invalid_scope error
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(redirect_url)
            assert parsed.scheme == "https"
            assert parsed.netloc == "example.com"
            assert parsed.path == "/callback"
            query_params = parse_qs(parsed.query)

            # Verify error parameters
            assert "error" in query_params
            assert query_params["error"][0] == "invalid_scope"
            assert "error_description" in query_params
            assert "Requested scopes are not available" in query_params["error_description"][0]

            # Verify state parameter is echoed back
            assert "state" in query_params
            assert query_params["state"][0] == "client_state_456"

            # Verify NO authorization code was created
            assert len(authorization_codes_storage) == 0

            # Cleanup
            app.dependency_overrides = {}

    @patch("auth_server.routes.oauth_flow.map_groups_to_scopes")
    def test_backward_compatibility_without_resolved_scope(
        self, mock_map_groups, test_client_with_user_service: TestClient, clear_device_storage
    ):
        """Test that old authorization codes without resolved_scope still work."""
        # Setup mocks
        default_scopes = ["servers-read"]
        mock_map_groups.return_value = default_scopes

        # Create authorization code WITHOUT resolved_scope field (old format)
        client_id = "test-client"
        redirect_uri = "https://example.com/callback"
        auth_code, user_info = self._create_mock_callback_state(client_id, redirect_uri)

        # Remove resolved_scope to simulate old code
        del authorization_codes_storage[auth_code]["resolved_scope"]

        # Token request should still work
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": TEST_CODE_VERIFIER,
            },
        )

        assert response.status_code == 200
        token_data = response.json()

        # Should fall back to computing from groups
        access_token = token_data["access_token"]
        jwt_payload = decode_jwt_unverified(access_token)
        assert jwt_payload["scope"] == " ".join(default_scopes)


@pytest.mark.integration
@pytest.mark.oauth_flow
class TestRefreshTokenRotation:
    """Integration tests for refresh token rotation (OAuth 2.1)."""

    def test_refresh_token_rotation_generates_new_token(
        self, test_client_with_user_service: TestClient, clear_device_storage
    ):
        """Test that using a refresh token generates a new refresh token."""

        # Setup initial refresh token
        client_id = "test-client"
        old_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())

        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "groups": ["jarvis-registry-admin"],
        }

        refresh_tokens_storage[old_refresh_token] = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": "servers-read agents-read",
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }

        # Use refresh token to get new access token
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh_token,
                "client_id": client_id,
            },
        )

        assert response.status_code == 200
        token_data = response.json()

        # Verify new access token
        assert "access_token" in token_data
        assert token_data["token_type"] == "Bearer"

        # Verify new refresh token is different
        new_refresh_token = token_data["refresh_token"]
        assert new_refresh_token is not None
        assert new_refresh_token != old_refresh_token

        # Verify old refresh token is deleted
        assert old_refresh_token not in refresh_tokens_storage

        # Verify new refresh token exists and has extended expiry
        assert new_refresh_token in refresh_tokens_storage
        new_token_data = refresh_tokens_storage[new_refresh_token]
        assert new_token_data["client_id"] == client_id
        assert new_token_data["user_info"] == user_info
        assert new_token_data["scope"] == "servers-read agents-read"

        # Verify expiry is extended
        assert new_token_data["expires_at"] > current_time + 1209500  # Close to 14 days

    def test_refresh_token_requires_client_consent(
        self,
        test_client_with_user_service: TestClient,
        clear_device_storage,
        mock_user_service,
    ):
        """Existing refresh tokens must not mint new access tokens until the user consents."""
        client_id = "test-client"
        old_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())
        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "groups": ["jarvis-registry-admin"],
        }
        refresh_tokens_storage[old_refresh_token] = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": "servers-read agents-read",
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }

        test_consent_store.default_client_consent = False
        try:
            response = test_client_with_user_service.post(
                f"{API_PREFIX}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": old_refresh_token,
                    "client_id": client_id,
                },
            )
        finally:
            test_consent_store.default_client_consent = True

        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "interaction_required"
        assert "consent" in body["error_description"].lower()
        assert old_refresh_token in refresh_tokens_storage
        assert len(refresh_tokens_storage) == 1
        mock_user_service.resolve_user_id.assert_awaited_once_with(user_info)

    def test_refresh_token_with_client_consent_rotates(
        self,
        test_client_with_user_service: TestClient,
        clear_device_storage,
    ):
        """A prior consent record allows refresh-token rotation to continue."""
        client_id = "test-client"
        old_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())
        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "groups": ["jarvis-registry-admin"],
        }
        refresh_tokens_storage[old_refresh_token] = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": "servers-read agents-read",
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }
        test_consent_store.default_client_consent = False
        test_consent_store.grant_client_consent("user-id-123", client_id)
        try:
            response = test_client_with_user_service.post(
                f"{API_PREFIX}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": old_refresh_token,
                    "client_id": client_id,
                },
            )
        finally:
            test_consent_store.default_client_consent = True

        assert response.status_code == 200
        new_refresh_token = response.json()["refresh_token"]
        assert old_refresh_token not in refresh_tokens_storage
        assert new_refresh_token in refresh_tokens_storage

    def test_old_refresh_token_cannot_be_reused(self, test_client_with_user_service: TestClient, clear_device_storage):
        """Test that after rotation, old refresh token is invalidated."""

        # Setup initial refresh token
        client_id = "test-client"
        old_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())

        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "groups": ["jarvis-registry-admin"],
        }

        refresh_tokens_storage[old_refresh_token] = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": "servers-read",
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }

        # First use: should succeed
        response1 = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh_token,
                "client_id": client_id,
            },
        )

        assert response1.status_code == 200

        # Second use: should fail (token rotated)
        response2 = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh_token,
                "client_id": client_id,
            },
        )

        assert response2.status_code == 400
        error_data = response2.json()
        assert error_data["error"] == "invalid_grant"

    def test_refresh_token_rotation_chain(self, test_client_with_user_service: TestClient, clear_device_storage):
        """Test that refresh tokens can be rotated multiple times in a chain."""

        client_id = "test-client"
        current_time = int(time.time())

        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "groups": ["jarvis-registry-admin"],
        }

        # Start with initial refresh token
        rt1 = secrets.token_urlsafe(32)
        refresh_tokens_storage[rt1] = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": "servers-read",
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }

        # First rotation
        response1 = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={"grant_type": "refresh_token", "refresh_token": rt1, "client_id": client_id},
        )
        assert response1.status_code == 200
        rt2 = response1.json()["refresh_token"]
        assert rt2 != rt1
        assert rt1 not in refresh_tokens_storage
        assert rt2 in refresh_tokens_storage

        # Second rotation
        response2 = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={"grant_type": "refresh_token", "refresh_token": rt2, "client_id": client_id},
        )
        assert response2.status_code == 200
        rt3 = response2.json()["refresh_token"]
        assert rt3 != rt2
        assert rt2 not in refresh_tokens_storage
        assert rt3 in refresh_tokens_storage

        # Third rotation
        response3 = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={"grant_type": "refresh_token", "refresh_token": rt3, "client_id": client_id},
        )
        assert response3.status_code == 200
        rt4 = response3.json()["refresh_token"]
        assert rt4 != rt3
        assert rt3 not in refresh_tokens_storage
        assert rt4 in refresh_tokens_storage

    def test_refresh_token_scope_preserved(self, test_client_with_user_service: TestClient, clear_device_storage):
        """Test that scope is preserved when rotating refresh tokens."""

        client_id = "test-client"
        original_scope = "servers-read agents-read servers-write"
        old_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())

        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "groups": ["jarvis-registry-admin"],
        }

        refresh_tokens_storage[old_refresh_token] = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": original_scope,
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }

        # Use refresh token
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh_token,
                "client_id": client_id,
            },
        )

        assert response.status_code == 200
        token_data = response.json()

        # Verify scope preserved in response
        assert token_data["scope"] == original_scope

        # Verify scope preserved in new refresh token
        new_refresh_token = token_data["refresh_token"]
        new_token_data = refresh_tokens_storage[new_refresh_token]
        assert new_token_data["scope"] == original_scope

        # Verify scope in access token JWT
        access_token = token_data["access_token"]
        jwt_payload = decode_jwt_unverified(access_token)
        assert jwt_payload["scope"] == original_scope

    def test_refresh_token_invalid_token(self, test_client_with_user_service: TestClient, clear_device_storage):
        """Test that using an invalid refresh token returns error."""
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "invalid-token",
                "client_id": "test-client",
            },
        )

        assert response.status_code == 400
        error_data = response.json()
        assert error_data["error"] == "invalid_grant"

    def test_refresh_token_expired(self, test_client_with_user_service: TestClient, clear_device_storage):
        """Test that expired refresh token is rejected and removed."""

        client_id = "test-client"
        expired_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())

        # Create expired refresh token
        refresh_tokens_storage[expired_refresh_token] = {
            "client_id": client_id,
            "user_info": {"username": "test_user", "email": "test@example.com", "groups": []},
            "scope": "servers-read",
            "expires_at": current_time - 1,  # Expired
        }

        # Try to use expired token
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": expired_refresh_token,
                "client_id": client_id,
            },
        )

        assert response.status_code == 400
        error_data = response.json()
        assert error_data["error"] == "invalid_grant"

        # Verify expired token was removed
        assert expired_refresh_token not in refresh_tokens_storage

    def test_refresh_token_client_id_mismatch(self, test_client_with_user_service: TestClient, clear_device_storage):
        """Test that refresh token with mismatched client_id is rejected."""
        old_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())

        # Create refresh token for client-1
        refresh_tokens_storage[old_refresh_token] = {
            "client_id": "client-1",
            "user_info": {"username": "test_user", "email": "test@example.com", "groups": []},
            "scope": "servers-read",
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }

        # Try to use with client-2
        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh_token,
                "client_id": "client-2",
            },
        )

        assert response.status_code == 400
        error_data = response.json()
        assert error_data["error"] == "invalid_client"

        # Verify token not removed (not expired, just wrong client)
        assert old_refresh_token in refresh_tokens_storage

    def test_refresh_token_invalid_client_secret(self, test_client_with_user_service: TestClient, clear_device_storage):
        """Test client_secret_post clients must provide the registered secret for refresh_token grant."""
        client_id = "secret-client"
        old_refresh_token = secrets.token_urlsafe(32)
        current_time = int(time.time())

        test_oauth_state_store.save_client(
            client_id,
            {
                "client_id": client_id,
                "client_secret": "correct-secret",
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        refresh_tokens_storage[old_refresh_token] = {
            "client_id": client_id,
            "user_info": {"username": "test_user", "email": "test@example.com", "groups": []},
            "scope": "servers-read",
            "expires_at": current_time + REFRESH_TOKEN_EXPIRY_SECONDS,
        }

        response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh_token,
                "client_id": client_id,
                "client_secret": "wrong-secret",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client"
        assert old_refresh_token in refresh_tokens_storage


@pytest.mark.integration
@pytest.mark.oauth_flow
class TestScopingAndRotationIntegration:
    """Integration tests combining scoping and rotation."""

    @patch("auth_server.routes.oauth_flow.map_groups_to_scopes")
    def test_scoped_token_refresh_preserves_scope(
        self, mock_map_groups, test_client_with_user_service: TestClient, clear_device_storage
    ):
        """Test that refreshing a scoped access token preserves the negotiated scope."""
        default_scopes = ["servers-read", "agents-read", "servers-write", "agents-write"]
        mock_map_groups.return_value = default_scopes

        # Simulate authorization code flow with scoped token
        client_id = "test-client"
        redirect_uri = "https://example.com/callback"
        negotiated_scopes = ["servers-read", "agents-read"]  # Subset requested by client

        # Create authorization code with resolved scope
        auth_code = secrets.token_urlsafe(32)
        current_time = int(time.time())

        user_info = {
            "username": "test_user",
            "email": "test@example.com",
            "name": "Test User",
            "idp_id": "test-idp-id",
            "groups": ["jarvis-registry-admin"],
        }

        authorization_codes_storage[auth_code] = {
            "token_data": {"access_token": "mock-idp-token"},
            "user_info": user_info,
            "client_id": client_id,
            "expires_at": current_time + 600,
            "used": False,
            "code_challenge": TEST_CODE_CHALLENGE,
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
            "resource": None,
            "created_at": current_time,
            "resolved_scope": negotiated_scopes,  # Scoped token
        }

        # Exchange auth code for tokens
        token_response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": TEST_CODE_VERIFIER,
            },
        )

        assert token_response.status_code == 200
        token_data = token_response.json()

        # Verify initial access token has negotiated scope
        access_token_1 = token_data["access_token"]
        jwt_payload_1 = decode_jwt_unverified(access_token_1)
        assert jwt_payload_1["scope"] == " ".join(negotiated_scopes)

        # Get refresh token
        refresh_token_1 = token_data["refresh_token"]
        assert refresh_token_1 is not None

        # Use refresh token to get new access token
        refresh_response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token_1,
                "client_id": client_id,
            },
        )

        assert refresh_response.status_code == 200
        refresh_data = refresh_response.json()

        # Verify refreshed access token still has negotiated scope (not full scope)
        access_token_2 = refresh_data["access_token"]
        jwt_payload_2 = decode_jwt_unverified(access_token_2)
        assert jwt_payload_2["scope"] == " ".join(negotiated_scopes)
        assert "servers-write" not in jwt_payload_2["scope"]
        assert "agents-write" not in jwt_payload_2["scope"]

        # Verify new refresh token was issued
        refresh_token_2 = refresh_data["refresh_token"]
        assert refresh_token_2 != refresh_token_1

        # Verify old refresh token cannot be reused
        reuse_response = test_client_with_user_service.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token_1,
                "client_id": client_id,
            },
        )
        assert reuse_response.status_code == 400
