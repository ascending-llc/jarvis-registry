"""
Unit tests for OAuth callback session handling.

Tests state parameter encoding/decoding and session expiration scenarios.
"""

import base64
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from auth_server.deps import get_oauth2_config, get_signer
from auth_server.server import app


@pytest.fixture
def mock_oauth_config():
    """Mock OAuth2 configuration"""
    return {
        "providers": {
            "entra": {
                "enabled": True,
                "client_id": "test-client-id",
                "client_secret": "test-secret",
                "auth_url": "https://login.test.com/authorize",
                "token_url": "https://login.test.com/token",
                "userinfo_url": "https://login.test.com/userinfo",
                "response_type": "code",
                "grant_type": "authorization_code",
                "scopes": ["openid", "profile", "email"],
                "username_claim": "preferred_username",
                "email_claim": "email",
                "name_claim": "name",
                "groups_claim": "groups",
                "display_name": "Microsoft Entra ID",
            }
        }
    }


@pytest.fixture
def mock_signer():
    return URLSafeTimedSerializer("test-secret-key")


class TestStateEncoding:
    """Test state parameter encoding and decoding"""

    def test_cookie_contains_resource_parameter(
        self, test_client: TestClient, mock_oauth_config, mock_signer: URLSafeTimedSerializer
    ):
        """Test that OAuth login encodes resource in state parameter"""
        resource_url = "https://jarvis-demo.ascendingdc.com/gateway/proxy/mcpgw/mcp"

        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth2_config] = lambda: mock_oauth_config
        app.dependency_overrides[get_signer] = lambda: mock_signer

        response = test_client.get(
            "/auth/oauth2/login/entra",
            params={
                "client_id": "test-client",
                "response_type": "code",
                "redirect_uri": "http://localhost/callback",
                "code_challenge": "test123",
                "code_challenge_method": "S256",
                "resource": resource_url,
            },
            follow_redirects=False,
        )

        assert response.status_code == 302

        # Extract state from redirect URL
        location = response.headers["location"]
        assert "state=" in location

        # Extract state parameter using urllib
        import urllib.parse

        parsed_url = urllib.parse.urlparse(location)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        state = query_params.get("state", [None])[0]
        assert state is not None

        # Decode state (it's base64-encoded JSON)
        state_padded = state + "=" * ((-len(state)) % 4)
        state_decoded = json.loads(base64.urlsafe_b64decode(state_padded).decode())

        # Verify resource is preserved
        assert "nonce" in state_decoded

        temp_session_cookie = response.cookies.get("oauth2_temp_session")
        assert temp_session_cookie is not None

        session_data = mock_signer.loads(temp_session_cookie, max_age=10 * 60)
        assert session_data["resource"] == resource_url

    def test_state_decoding_with_padding(self):
        """Test that state decoding handles padding correctly"""
        resource_url = "https://example.com/proxy/server"
        state_data = {"nonce": "test-nonce-12345", "resource": resource_url}

        # Encode without padding
        state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

        # Decode with padding
        state_padded = state + "=" * ((-len(state)) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(state_padded).decode())

        assert decoded["nonce"] == "test-nonce-12345"

    def test_cookie_without_resource_parameter(self, test_client, mock_oauth_config, mock_signer):
        """Test that OAuth login works without resource parameter"""
        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth2_config] = lambda: mock_oauth_config
        app.dependency_overrides[get_signer] = lambda: mock_signer

        response = test_client.get(
            "/auth/oauth2/login/entra",
            params={
                "client_id": "test-client",
                "response_type": "code",
                "redirect_uri": "http://localhost/callback",
                "code_challenge": "test123",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302

        # Extract and decode state
        location = response.headers["location"]
        import urllib.parse

        parsed_url = urllib.parse.urlparse(location)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        state = query_params.get("state", [None])[0]
        assert state is not None

        state_padded = state + "=" * ((-len(state)) % 4)
        state_decoded = json.loads(base64.urlsafe_b64decode(state_padded).decode())

        assert "nonce" in state_decoded

        # Resource should be None
        temp_session_cookie = response.cookies.get("oauth2_temp_session")
        assert temp_session_cookie is not None

        session_data = mock_signer.loads(temp_session_cookie, max_age=10 * 60)
        assert "resource" not in session_data


class TestSessionExpiration:
    """Test session expiration handling with 401 responses"""

    def test_signature_expired_returns_401_with_www_authenticate(self, test_client, mock_oauth_config):
        """Test that SignatureExpired returns 401 with WWW-Authenticate header"""
        # Create a state with resource
        resource_url = "https://jarvis-demo.ascendingdc.com/gateway/proxy/mcpgw"
        state_data = {"nonce": "test-nonce", "resource": resource_url}
        state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

        mock_signer = MagicMock(spec=URLSafeTimedSerializer)
        mock_signer.loads.side_effect = SignatureExpired("Session expired")

        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth2_config] = lambda: mock_oauth_config
        app.dependency_overrides[get_signer] = lambda: mock_signer

        response = test_client.get(
            "/auth/oauth2/callback/entra",
            params={"code": "fake_code", "state": state},
            cookies={"oauth2_temp_session": "expired_session"},
        )

        # Should return 401
        assert response.status_code == 401

        # Should have WWW-Authenticate header with resource_metadata
        assert "www-authenticate" in response.headers
        www_auth = response.headers["www-authenticate"]

        assert 'Bearer realm="jarvis-resources"' in www_auth

        # Check response body
        assert "OAuth session expired" in response.json()["detail"]

    def test_bad_signature_returns_401_with_www_authenticate(self, test_client, mock_oauth_config):
        """Test that BadSignature returns 401 (not 400) with WWW-Authenticate header"""
        # Create a state with resource
        resource_url = "https://jarvis-demo.ascendingdc.com/gateway/proxy/mcpgw"
        state_data = {"nonce": "test-nonce", "resource": resource_url}
        state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

        mock_signer = MagicMock(spec=URLSafeTimedSerializer)
        mock_signer.loads.side_effect = BadSignature("Invalid signature")

        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth2_config] = lambda: mock_oauth_config
        app.dependency_overrides[get_signer] = lambda: mock_signer

        response = test_client.get(
            "/auth/oauth2/callback/entra",
            params={"code": "fake_code", "state": state},
            cookies={"oauth2_temp_session": "invalid_session"},
        )

        # Should return 401 (not 400)
        assert response.status_code == 401

        # Should have WWW-Authenticate header with resource_metadata
        assert "www-authenticate" in response.headers
        www_auth = response.headers["www-authenticate"]

        assert 'Bearer realm="jarvis-resources"' in www_auth
        assert "resource_metadata" not in www_auth

        # Check response body (both SignatureExpired and BadSignature use same message)
        assert "OAuth session expired" in response.json()["detail"]


class TestMissingParameters:
    """Test handling of missing required parameters"""

    def test_missing_code_parameter(self, test_client, mock_oauth_config, mock_signer):
        """Test that missing code parameter returns 400"""

        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth2_config] = lambda: mock_oauth_config
        app.dependency_overrides[get_signer] = lambda: mock_signer

        response = test_client.get(
            "/auth/oauth2/callback/entra",
            params={"state": "test_state"},
            cookies={"oauth2_temp_session": "test_session"},
        )

        assert response.status_code == 400
        assert "Missing required OAuth2 parameters" in response.json()["detail"]

    def test_missing_state_parameter(self, test_client, mock_oauth_config, mock_signer):
        """Test that missing state parameter returns 400"""

        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth2_config] = lambda: mock_oauth_config
        app.dependency_overrides[get_signer] = lambda: mock_signer

        response = test_client.get(
            "/auth/oauth2/callback/entra",
            params={"code": "test_code"},
            cookies={"oauth2_temp_session": "test_session"},
        )

        assert response.status_code == 400
        assert "Missing required OAuth2 parameters" in response.json()["detail"]

    def test_missing_session_cookie(self, test_client, mock_oauth_config, mock_signer):
        """Test that missing session cookie returns 400"""
        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth2_config] = lambda: mock_oauth_config
        app.dependency_overrides[get_signer] = lambda: mock_signer

        response = test_client.get("/auth/oauth2/callback/entra", params={"code": "test_code", "state": "test_state"})

        assert response.status_code == 400
        assert "Missing required OAuth2 parameters" in response.json()["detail"]
