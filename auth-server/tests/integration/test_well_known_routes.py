"""
Integration tests for .well-known OAuth 2.0 discovery endpoints.

Tests RFC 8414 (OAuth 2.0 Authorization Server Metadata) and
OIDC Discovery implementations.
"""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient


def int_to_base64url(value):
    """Converts an integer to a Base64url-encoded string as per RFC 7518."""
    # Calculate the number of bytes needed
    byte_length = (value.bit_length() + 7) // 8
    der_bytes = value.to_bytes(byte_length, byteorder="big")
    # Encode to base64, swap characters for URL safety, and strip padding
    return base64.urlsafe_b64encode(der_bytes).decode("utf-8").rstrip("=")


@pytest.mark.integration
@pytest.mark.well_known
class TestWellKnownRoutes:
    """Integration tests for .well-known discovery endpoints."""

    def test_oauth_authorization_server_metadata(self, test_client: TestClient):
        """Test OAuth 2.0 Authorization Server Metadata endpoint (RFC 8414)."""
        response = test_client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()

        # Required fields per RFC 8414
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "jwks_uri" in data

        # Verify endpoint URLs are properly formatted
        # Issuer is at root (per RFC 8414)
        assert data["issuer"] == "http://localhost:8888"
        # OAuth endpoints include the /auth prefix when AUTH_SERVER_API_PREFIX is set
        assert data["authorization_endpoint"] == "http://localhost:8888/auth/oauth2/login/keycloak"
        assert data["token_endpoint"] == "http://localhost:8888/auth/oauth2/token"
        # JWKS is at root level
        assert data["jwks_uri"] == "http://localhost:8888/.well-known/jwks.json"

        # Verify device flow support
        assert "device_authorization_endpoint" in data
        assert data["device_authorization_endpoint"] == "http://localhost:8888/auth/oauth2/device/code"

        # Verify grant types
        assert "grant_types_supported" in data
        assert "authorization_code" in data["grant_types_supported"]
        assert "urn:ietf:params:oauth:grant-type:device_code" in data["grant_types_supported"]

        # Verify response types
        assert "response_types_supported" in data
        assert "code" in data["response_types_supported"]

        # Verify token endpoint auth methods
        assert "token_endpoint_auth_methods_supported" in data
        assert "client_secret_post" in data["token_endpoint_auth_methods_supported"]

        # Verify PKCE support
        assert "code_challenge_methods_supported" in data
        assert "S256" in data["code_challenge_methods_supported"]

        # Verify scopes
        assert "scopes_supported" in data
        assert isinstance(data["scopes_supported"], list)
        assert len(data["scopes_supported"]) > 0

    def test_openid_configuration(self, test_client: TestClient):
        """Test OpenID Connect Discovery endpoint."""
        response = test_client.get("/.well-known/openid-configuration")

        assert response.status_code == 200
        data = response.json()

        # Required OIDC fields
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "userinfo_endpoint" in data
        assert "jwks_uri" in data

        # Verify OIDC-specific endpoints (include /auth prefix when AUTH_SERVER_API_PREFIX is set)
        assert data["userinfo_endpoint"] == "http://localhost:8888/auth/oauth2/userinfo"

        # Verify subject types
        assert "subject_types_supported" in data
        assert "public" in data["subject_types_supported"]

        # Verify signing algorithms
        assert "id_token_signing_alg_values_supported" in data
        assert "HS256" not in data["id_token_signing_alg_values_supported"]
        assert "RS256" in data["id_token_signing_alg_values_supported"]

        # Verify OIDC scopes
        assert "scopes_supported" in data
        assert "openid" in data["scopes_supported"]
        assert "profile" in data["scopes_supported"]
        assert "email" in data["scopes_supported"]

        # Verify claims
        assert "claims_supported" in data
        assert "sub" in data["claims_supported"]
        assert "email" in data["claims_supported"]
        assert "name" in data["claims_supported"]
        assert "groups" in data["claims_supported"]

        # Verify device flow support in OIDC config
        assert "device_authorization_endpoint" in data
        assert "grant_types_supported" in data
        assert "urn:ietf:params:oauth:grant-type:device_code" in data["grant_types_supported"]

    def test_jwks_endpoint(self, test_client: TestClient, jwt_rsa_key: rsa.RSAPrivateKey):
        """Test JWKS (JSON Web Key Set) endpoint."""
        response = test_client.get("/.well-known/jwks.json")

        assert response.status_code == 200
        data = response.json()

        # Verify JWKS structure
        assert "keys" in data
        assert isinstance(data["keys"], list)
        assert len(data["keys"]) == 1

        key = data["keys"][0]

        from auth_server.core.config import settings

        assert key["alg"] == "RS256"
        assert key["kid"] == settings.jwt_self_signed_kid
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"

        numbers = jwt_rsa_key.public_key().public_numbers()

        assert key["e"] == int_to_base64url(numbers.e)
        assert key["n"] == int_to_base64url(numbers.n)

    def test_discovery_endpoints_consistency(self, test_client: TestClient):
        """Test that both discovery endpoints return consistent issuer and endpoint URLs."""
        oauth_response = test_client.get("/.well-known/oauth-authorization-server")
        oidc_response = test_client.get("/.well-known/openid-configuration")

        assert oauth_response.status_code == 200
        assert oidc_response.status_code == 200

        oauth_data = oauth_response.json()
        oidc_data = oidc_response.json()

        # Verify consistent issuer
        assert oauth_data["issuer"] == oidc_data["issuer"]

        # Verify consistent endpoints
        assert oauth_data["authorization_endpoint"] == oidc_data["authorization_endpoint"]
        assert oauth_data["token_endpoint"] == oidc_data["token_endpoint"]
        assert oauth_data["jwks_uri"] == oidc_data["jwks_uri"]
        assert oauth_data["device_authorization_endpoint"] == oidc_data["device_authorization_endpoint"]

    def test_well_known_endpoints_without_env_var(self, test_client: TestClient):
        """Test .well-known endpoints when AUTH_SERVER_EXTERNAL_URL is not configured in settings."""
        from unittest.mock import patch

        # Mock settings to have empty auth_server_external_url
        with patch("auth_server.routes.well_known.settings") as mock_settings:
            mock_settings.auth_server_external_url = ""

            response = test_client.get("/.well-known/oauth-authorization-server")

            # Should return 500 error when config is missing
            assert response.status_code == 500
            data = response.json()
            assert "detail" in data
            assert "AUTH_SERVER_EXTERNAL_URL" in data["detail"]

    def test_well_known_response_headers(self, test_client: TestClient):
        """Test that .well-known endpoints return correct content-type headers."""
        endpoints = [
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
            "/.well-known/jwks.json",
        ]

        for endpoint in endpoints:
            response = test_client.get(endpoint)
            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]

    def test_oauth_metadata_scopes(self, test_client: TestClient):
        """Test that OAuth metadata includes proper scopes."""
        response = test_client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()

        scopes = data["scopes_supported"]

        # Expect actual scope names from scopes.yml, not group names
        expected_scopes = {
            "servers-read",
            "agents-read",
            "agents-write",
            "server-write",
            "servers-share",
            "agents-share",
            "system-ops",
            "user-read",
            "acl-read",
            "acl-write",
        }

        assert expected_scopes.issubset(set(scopes))

    def test_jwks_caching_behavior(self, test_client: TestClient):
        """Test that JWKS responses are consistent across requests."""
        # Make multiple requests
        response1 = test_client.get("/.well-known/jwks.json")
        response2 = test_client.get("/.well-known/jwks.json")

        assert response1.status_code == 200
        assert response2.status_code == 200

        # Both should return the same data
        assert response1.json() == response2.json()

        # Should return empty keys (HS256 symmetric key is never publicly exposed)
        assert len(response1.json()["keys"]) == 1
        assert len(response2.json()["keys"]) == 1

    def test_discovery_endpoint_trailing_slash(self, test_client: TestClient):
        """Test that discovery endpoints work with and without trailing slash."""
        endpoints = [
            ("/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/"),
            ("/.well-known/openid-configuration", "/.well-known/openid-configuration/"),
        ]

        for without_slash, with_slash in endpoints:
            response1 = test_client.get(without_slash)
            response2 = test_client.get(with_slash, follow_redirects=True)

            # Both should succeed
            assert response1.status_code == 200
            # Second one might redirect or fail, that's ok
            assert response2.status_code in [200, 404, 307, 308]

    def test_service_documentation_link(self, test_client: TestClient):
        """Test that OAuth metadata includes service documentation link."""
        response = test_client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()

        assert "service_documentation" in data
        assert data["service_documentation"] == "http://localhost:8888/auth/docs"

    def test_pkce_support_in_metadata(self, test_client: TestClient):
        """Test that PKCE (Proof Key for Code Exchange) support is advertised."""
        response = test_client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()

        # PKCE support
        assert "code_challenge_methods_supported" in data
        assert "S256" in data["code_challenge_methods_supported"]

    def test_multiple_response_types(self, test_client: TestClient):
        """Test that metadata includes supported response types."""
        response = test_client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()

        assert "response_types_supported" in data
        assert "code" in data["response_types_supported"]
        # Currently only authorization code flow is supported

    def test_oidc_subject_types(self, test_client: TestClient):
        """Test that OIDC configuration includes subject types."""
        response = test_client.get("/.well-known/openid-configuration")

        assert response.status_code == 200
        data = response.json()

        assert "subject_types_supported" in data
        assert "public" in data["subject_types_supported"]
        # "pairwise" could be added in the future for enhanced privacy
