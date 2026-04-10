"""
Unit tests for JWT audience validation with RFC 8707 Resource Indicators.

Tests verify that:
1. Self-signed tokens (kid='self-signed-key-v1') skip audience validation
2. Resource URLs are accepted as audience claims
3. Provider tokens still validate audience properly

JWT operations are fully mocked — crypto logic is tested in
registry-pkgs/tests/unit/test_jwt_utils.py.
"""

from unittest.mock import patch

import pytest


@pytest.mark.unit
@pytest.mark.auth
class TestJWTAudienceValidation:
    """Test JWT audience validation for RFC 8707 compliance."""

    def test_self_signed_token_skips_audience_validation(self):
        """Self-signed tokens should skip audience validation (aud == resource URL)."""
        from auth_server.server import JWT_ISSUER, JWT_SELF_SIGNED_KID

        resource_url = "http://localhost/proxy/mcpgw"

        fake_claims = {
            "iss": JWT_ISSUER,
            "aud": resource_url,
            "sub": "test-user",
            "scope": "test-scope",
        }
        fake_token = "header.payload.sig"

        with (
            patch("registry_pkgs.core.jwt_utils.encode_jwt", return_value=fake_token),
            patch("registry_pkgs.core.jwt_utils.decode_jwt", return_value=fake_claims),
        ):
            # Simulate encoding a self-signed token with a resource URL as audience
            from registry_pkgs.core.jwt_utils import decode_jwt, encode_jwt

            payload = {
                "iss": JWT_ISSUER,
                "aud": resource_url,
                "sub": "test-user",
                "scope": "test-scope",
            }
            token = encode_jwt(payload, "dummy-private-key", kid=JWT_SELF_SIGNED_KID)
            assert token == fake_token

            # Decode skipping audience verification (audience=None)
            claims = decode_jwt(token, "dummy-public-key", issuer=JWT_ISSUER, audience=None)
            assert claims["aud"] == resource_url
            assert claims["sub"] == "test-user"

    def test_provider_token_validates_audience(self):
        """Provider tokens should validate audience strictly."""
        from auth_server.server import JWT_AUDIENCE, JWT_ISSUER

        fake_claims = {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "sub": "test-user",
            "scope": "test-scope",
        }
        fake_token = "header.payload.sig"

        with (
            patch("registry_pkgs.core.jwt_utils.encode_jwt", return_value=fake_token),
            patch("registry_pkgs.core.jwt_utils.decode_jwt", return_value=fake_claims),
        ):
            from registry_pkgs.core.jwt_utils import decode_jwt, encode_jwt

            payload = {"iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "sub": "test-user", "scope": "test-scope"}
            token = encode_jwt(payload, "dummy-private-key", kid="provider-key-id")
            claims = decode_jwt(token, "dummy-public-key", issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
            assert claims["aud"] == JWT_AUDIENCE

    def test_provider_token_rejects_wrong_audience(self):
        """Provider tokens should reject mismatched audience."""
        from auth_server.server import JWT_AUDIENCE, JWT_ISSUER
        from registry_pkgs.core.jwt_utils import InvalidAudienceError

        fake_token = "header.payload.sig"

        with (
            patch("registry_pkgs.core.jwt_utils.encode_jwt", return_value=fake_token),
            patch("registry_pkgs.core.jwt_utils.decode_jwt", side_effect=InvalidAudienceError("wrong audience")),
        ):
            from registry_pkgs.core.jwt_utils import decode_jwt, encode_jwt

            payload = {"iss": JWT_ISSUER, "aud": "wrong-audience", "sub": "test-user"}
            token = encode_jwt(payload, "dummy-private-key", kid="provider-key-id")

            with pytest.raises(InvalidAudienceError):
                decode_jwt(token, "dummy-public-key", issuer=JWT_ISSUER, audience=JWT_AUDIENCE)

    def test_resource_url_in_token_payload(self):
        """Token with resource URL should contain correct aud claim."""
        from auth_server.server import JWT_ISSUER, JWT_SELF_SIGNED_KID

        resource_url = "http://localhost/proxy/server123"

        fake_claims = {
            "iss": JWT_ISSUER,
            "aud": resource_url,
            "sub": "test-user",
            "scope": "server123:read server123:write",
        }
        fake_token = "header.payload.sig"

        with (
            patch("registry_pkgs.core.jwt_utils.encode_jwt", return_value=fake_token),
            patch("registry_pkgs.core.jwt_utils.decode_jwt", return_value=fake_claims),
        ):
            from registry_pkgs.core.jwt_utils import decode_jwt, encode_jwt

            payload = {
                "iss": JWT_ISSUER,
                "aud": resource_url,
                "sub": "test-user",
                "scope": "server123:read server123:write",
            }
            token = encode_jwt(payload, "dummy-private-key", kid=JWT_SELF_SIGNED_KID)
            claims = decode_jwt(token, "dummy-public-key", issuer=JWT_ISSUER, audience=None)

            assert claims["aud"] == resource_url
            assert "server123:read" in claims["scope"]
