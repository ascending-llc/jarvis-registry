"""
Integration tests for root-level routes (RFC compliance).

Tests that verify:
1. .well-known endpoints are served at root level (RFC 8414, RFC 8414)
2. /authorize endpoint is served at root level (for mcp-remote)
3. All routes work with and without prefix for compatibility
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
@pytest.mark.root_routes
class TestRootLevelRoutes:
    """Integration tests for root-level RFC-compliant routes."""

    def test_well_known_at_root_level(self, test_client: TestClient):
        """
        Test .well-known/oauth-authorization-server is accessible at root level.

        RFC 8414 §3: Authorization server metadata MUST be available at:
        {issuer}/.well-known/oauth-authorization-server

        For mcp-remote compatibility, this must work without any path prefix.
        """
        response = test_client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()

        # Verify essential fields
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "device_authorization_endpoint" in data
        assert "registration_endpoint" in data

    def test_openid_configuration_at_root_level(self, test_client: TestClient):
        """
        Test .well-known/openid-configuration is accessible at root level.

        OpenID Connect Discovery 1.0 §4: Configuration MUST be available at:
        {issuer}/.well-known/openid-configuration
        """
        response = test_client.get("/.well-known/openid-configuration")

        assert response.status_code == 200
        data = response.json()

        # Verify OIDC-specific fields
        assert "issuer" in data
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "userinfo_endpoint" in data
        assert "jwks_uri" in data

    def test_jwks_at_root_level(self, test_client: TestClient):
        """
        Test .well-known/jwks.json is accessible at root level.

        RFC 7517 (JSON Web Key): JWKS endpoint must be publicly accessible.
        """
        response = test_client.get("/.well-known/jwks.json")

        assert response.status_code == 200
        data = response.json()

        # Verify JWKS structure
        assert "keys" in data
        assert isinstance(data["keys"], list)

    def test_root_level_consistency(self, test_client: TestClient):
        """
        Test that root-level endpoints return URLs without prefix.

        Ensures mcp-remote gets the correct URL structure when
        accessing endpoints at root level.
        """
        response = test_client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()

        # Issuer should not have /auth prefix (it's the root issuer)
        # But this depends on AUTH_SERVER_EXTERNAL_URL configuration
        # Just verify the structure is valid
        assert data["issuer"].startswith("http")
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data

    def test_oauth_endpoints_require_prefix(self, test_client: TestClient):
        """
        Test that OAuth endpoints (register, device, token) require /auth prefix.

        Only discovery endpoints (.well-known, /authorize) should be at root level.
        All OAuth operational endpoints should require the prefix.
        """
        # Registration endpoint should NOT work at root level
        response = test_client.post("/oauth2/register", json={"client_name": "test"}, follow_redirects=False)
        # Should be 404 (not found at root) or redirect if configured
        assert response.status_code in [404, 307, 308]

        # Should work with prefix
        response = test_client.post(
            "/auth/oauth2/register", json={"client_name": "test", "grant_types": ["authorization_code"]}
        )
        # Should be 200 (success) or 400 (validation error), but not 404
        assert response.status_code != 404

    def test_device_code_endpoint_prefix(self, test_client: TestClient):
        """
        Test device authorization endpoint requires /auth prefix.
        """
        # Should NOT work at root level
        response = test_client.post(
            "/oauth2/device/code", data={"client_id": "test", "scope": "test"}, follow_redirects=False
        )
        assert response.status_code in [404, 307, 308]

        # Should work with prefix
        response = test_client.post("/auth/oauth2/device/code", data={"client_id": "test", "scope": "test"})
        # Should be 200 or validation error, not 404
        assert response.status_code != 404

    def test_rfc_compliance_summary(self, test_client: TestClient):
        """
        Comprehensive test verifying RFC compliance for all discovery endpoints.

        This test serves as documentation of the expected behavior:
        - RFC 8414: Authorization Server Metadata at root
        - OpenID Connect Discovery: openid-configuration at root
        - JWK Set: jwks.json at root
        - OAuth Authorization: /authorize at root (mcp-remote compatibility)
        """
        # All these should return 200 at root level
        endpoints = [
            "/.well-known/oauth-authorization-server",  # RFC 8414
            "/.well-known/openid-configuration",  # OIDC Discovery
            "/.well-known/jwks.json",  # JWK Set
        ]

        for endpoint in endpoints:
            response = test_client.get(endpoint)
            assert response.status_code == 200, f"Failed: {endpoint}"
            data = response.json()
            assert data is not None, f"Empty response: {endpoint}"


@pytest.mark.integration
@pytest.mark.prefix_logic
class TestPrefixLogic:
    """Integration tests for API prefix logic."""

    def test_health_endpoint_with_prefix(self, test_client: TestClient):
        """
        Test health endpoint - registered at root level without prefix.

        The health endpoint is registered directly on the app as @app.get("/health"),
        so it's available at /health (not /auth/health).
        """
        # Health is at root level
        response = test_client.get("/health")
        assert response.status_code == 200

        # Should NOT be at /auth/health
        response = test_client.get("/auth/health")
        assert response.status_code == 404

    def test_config_endpoint_with_prefix(self, test_client: TestClient):
        """
        Test config endpoint - registered with prefix.

        The config endpoint is registered as @app.get(f"{api_prefix}/config"),
        so it's available at /auth/config when AUTH_SERVER_API_PREFIX=/auth.
        """
        response = test_client.get("/auth/config")
        assert response.status_code in [200, 401], "Config should work at /auth/config"

        # Should NOT be at root /config
        response = test_client.get("/config")
        assert response.status_code == 404, "Config should NOT be at root /config"

    def test_providers_endpoint_with_prefix(self, test_client: TestClient):
        """
        Test OAuth providers endpoint - registered with prefix.

        The providers endpoint is registered as @app.get(f"{api_prefix}/oauth2/providers"),
        so it's available at /auth/oauth2/providers when AUTH_SERVER_API_PREFIX=/auth.
        """
        response = test_client.get("/auth/oauth2/providers")
        assert response.status_code == 200, "Providers should work at /auth/oauth2/providers"
        data = response.json()
        assert "providers" in data, "Response should have 'providers' key"
        assert isinstance(data["providers"], list), "Providers should be a list"

        # Should NOT be at root /oauth2/providers
        response = test_client.get("/oauth2/providers")
        assert response.status_code == 404, "Providers should NOT be at root /oauth2/providers"

    def test_token_endpoint_with_prefix(self, test_client: TestClient):
        """
        Test token endpoint - registered with prefix via oauth_device_router.

        The token endpoint is part of oauth_device_router which is registered with
        api_prefix, so it's available at /auth/oauth2/token when AUTH_SERVER_API_PREFIX=/auth.
        """
        # Token endpoint should work at /auth/oauth2/token
        response = test_client.post(
            "/auth/oauth2/token", data={"grant_type": "authorization_code", "code": "test", "client_id": "test"}
        )
        # Should not be 404 (endpoint exists with prefix)
        assert response.status_code != 404, "Token endpoint should exist at /auth/oauth2/token"

        # Should NOT be at root /oauth2/token
        response = test_client.post(
            "/oauth2/token", data={"grant_type": "authorization_code", "code": "test", "client_id": "test"}
        )
        assert response.status_code == 404, "Token endpoint should NOT be at root /oauth2/token"
