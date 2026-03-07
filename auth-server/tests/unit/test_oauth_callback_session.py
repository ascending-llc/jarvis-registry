"""
Unit tests for OAuth callback session handling.

Tests state parameter encoding/decoding and session expiration scenarios.
"""

import base64
import json
from unittest.mock import PropertyMock, patch

import pytest
from fastapi.testclient import TestClient
from itsdangerous import BadSignature, SignatureExpired

from auth_server.core.config import AuthSettings
from auth_server.server import app


@pytest.fixture
def client():
    """Create test client"""
    return TestClient(app)


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
        },
        "registry": {"success_redirect": "/dashboard", "error_redirect": "/login"},
    }


class TestStateEncoding:
    """Test state parameter encoding and decoding"""

    def test_state_contains_resource_parameter(self, client, mock_oauth_config):
        """Test that OAuth login encodes resource in state parameter"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            resource_url = "https://jarvis-demo.ascendingdc.com/gateway/proxy/mcpgw"

            response = client.get(
                "/auth/oauth2/login/entra",
                params={
                    "client_id": "test-client",
                    "response_type": "code",
                    "redirect_uri": "http://localhost/callback",
                    "code_challenge": "test123",
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
            assert "resource" in state_decoded
            assert state_decoded["resource"] == resource_url

    def test_state_decoding_with_padding(self):
        """Test that state decoding handles padding correctly"""
        resource_url = "https://example.com/proxy/server"
        state_data = {"nonce": "test-nonce-12345", "resource": resource_url}

        # Encode without padding
        state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

        # Decode with padding
        state_padded = state + "=" * ((-len(state)) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(state_padded).decode())

        assert decoded["resource"] == resource_url
        assert decoded["nonce"] == "test-nonce-12345"

    def test_state_without_resource_parameter(self, client, mock_oauth_config):
        """Test that OAuth login works without resource parameter"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            response = client.get(
                "/auth/oauth2/login/entra",
                params={
                    "client_id": "test-client",
                    "response_type": "code",
                    "redirect_uri": "http://localhost/callback",
                    "code_challenge": "test123",
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

            # Resource should be None
            assert state_decoded["resource"] is None


class TestSessionExpiration:
    """Test session expiration handling with 401 responses"""

    def test_signature_expired_returns_401_with_www_authenticate(self, client, mock_oauth_config):
        """Test that SignatureExpired returns 401 with WWW-Authenticate header"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            # Create a state with resource
            resource_url = "https://jarvis-demo.ascendingdc.com/gateway/proxy/mcpgw"
            state_data = {"nonce": "test-nonce", "resource": resource_url}
            state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

            # Mock signer to raise SignatureExpired
            with patch("auth_server.routes.oauth_flow.signer") as mock_signer:
                mock_signer.loads.side_effect = SignatureExpired("Session expired")

                response = client.get(
                    "/auth/oauth2/callback/entra",
                    params={"code": "fake_code", "state": state},
                    cookies={"oauth2_temp_session": "expired_session"},
                )

                # Should return 401
                assert response.status_code == 401

                # Should have WWW-Authenticate header with resource_metadata
                assert "www-authenticate" in response.headers
                www_auth = response.headers["www-authenticate"]

                assert 'Bearer realm="jarvis-auth-server"' in www_auth
                assert (
                    'resource_metadata="https://jarvis-demo.ascendingdc.com/.well-known/oauth-protected-resource/gateway/proxy/mcpgw"'
                    in www_auth
                )

                # Check response body
                assert "OAuth session expired" in response.json()["detail"]

    def test_bad_signature_returns_401_with_www_authenticate(self, client, mock_oauth_config):
        """Test that BadSignature returns 401 (not 400) with WWW-Authenticate header"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            # Create a state with resource
            resource_url = "https://jarvis-demo.ascendingdc.com/gateway/proxy/mcpgw"
            state_data = {"nonce": "test-nonce", "resource": resource_url}
            state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

            # Mock signer to raise BadSignature
            with patch("auth_server.routes.oauth_flow.signer") as mock_signer:
                mock_signer.loads.side_effect = BadSignature("Invalid signature")

                response = client.get(
                    "/auth/oauth2/callback/entra",
                    params={"code": "fake_code", "state": state},
                    cookies={"oauth2_temp_session": "invalid_session"},
                )

                # Should return 401 (not 400)
                assert response.status_code == 401

                # Should have WWW-Authenticate header with resource_metadata
                assert "www-authenticate" in response.headers
                www_auth = response.headers["www-authenticate"]

                assert 'Bearer realm="jarvis-auth-server"' in www_auth
                assert (
                    'resource_metadata="https://jarvis-demo.ascendingdc.com/.well-known/oauth-protected-resource/gateway/proxy/mcpgw"'
                    in www_auth
                )

                # Check response body (both SignatureExpired and BadSignature use same message)
                assert "OAuth session expired" in response.json()["detail"]

    def test_session_expiration_without_resource(self, client, mock_oauth_config):
        """Test that session expiration without resource returns 401 without resource_metadata"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            # Create a state without resource
            state_data = {"nonce": "test-nonce", "resource": None}
            state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

            # Mock signer to raise SignatureExpired
            with patch("auth_server.routes.oauth_flow.signer") as mock_signer:
                mock_signer.loads.side_effect = SignatureExpired("Session expired")

                response = client.get(
                    "/auth/oauth2/callback/entra",
                    params={"code": "fake_code", "state": state},
                    cookies={"oauth2_temp_session": "expired_session"},
                )

                # Should return 401
                assert response.status_code == 401

                # Should have WWW-Authenticate header WITHOUT resource_metadata
                assert "www-authenticate" in response.headers
                www_auth = response.headers["www-authenticate"]

                assert 'Bearer realm="jarvis-auth-server"' in www_auth
                assert "resource_metadata" not in www_auth

    def test_resource_metadata_url_construction(self, client, mock_oauth_config):
        """Test correct construction of resource_metadata URL from resource parameter"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            test_cases = [
                {
                    "resource": "https://jarvis-demo.ascendingdc.com/gateway/proxy/mcpgw",
                    "expected_metadata": "https://jarvis-demo.ascendingdc.com/.well-known/oauth-protected-resource/gateway/proxy/mcpgw",
                },
                {
                    "resource": "https://example.com/proxy/server",
                    "expected_metadata": "https://example.com/.well-known/oauth-protected-resource/proxy/server",
                },
                {
                    "resource": "http://localhost/proxy/mcpgw",
                    "expected_metadata": "http://localhost/.well-known/oauth-protected-resource/proxy/mcpgw",
                },
            ]

            for test_case in test_cases:
                state_data = {"nonce": "test-nonce", "resource": test_case["resource"]}
                state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")

                with patch("auth_server.routes.oauth_flow.signer") as mock_signer:
                    mock_signer.loads.side_effect = SignatureExpired("Session expired")

                    response = client.get(
                        "/auth/oauth2/callback/entra",
                        params={"code": "fake_code", "state": state},
                        cookies={"oauth2_temp_session": "expired_session"},
                    )

                    assert response.status_code == 401
                    www_auth = response.headers["www-authenticate"]
                    assert test_case["expected_metadata"] in www_auth


class TestMissingParameters:
    """Test handling of missing required parameters"""

    def test_missing_code_parameter(self, client, mock_oauth_config):
        """Test that missing code parameter returns 400"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            response = client.get(
                "/auth/oauth2/callback/entra",
                params={"state": "test_state"},
                cookies={"oauth2_temp_session": "test_session"},
            )

            assert response.status_code == 400
            assert "Missing required OAuth2 parameters" in response.json()["detail"]

    def test_missing_state_parameter(self, client, mock_oauth_config):
        """Test that missing state parameter returns 400"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            response = client.get(
                "/auth/oauth2/callback/entra",
                params={"code": "test_code"},
                cookies={"oauth2_temp_session": "test_session"},
            )

            assert response.status_code == 400
            assert "Missing required OAuth2 parameters" in response.json()["detail"]

    def test_missing_session_cookie(self, client, mock_oauth_config):
        """Test that missing session cookie returns 400"""
        with patch.object(
            type(AuthSettings()), "oauth2_config", new_callable=PropertyMock, return_value=mock_oauth_config
        ):
            response = client.get("/auth/oauth2/callback/entra", params={"code": "test_code", "state": "test_state"})

            assert response.status_code == 400
            assert "Missing required OAuth2 parameters" in response.json()["detail"]
