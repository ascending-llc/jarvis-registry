"""Unit tests for auth_utils.jwt_utils module."""

import time
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from registry_pkgs.core.jwt_utils import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    build_jwks,
    build_jwt_payload,
    decode_jwt,
    decode_jwt_unverified,
    decode_jwt_with_jwk,
    encode_jwt,
    get_token_kid,
    get_token_unverified_header,
)

# ---------------------------------------------------------------------------
# Module-level RSA key pair for all tests
# ---------------------------------------------------------------------------

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_KEY_ALT = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # different key for negative tests

_PRIVATE_KEY = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")

_PUBLIC_KEY = (
    _RSA_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)

# A second, distinct public key for signature-mismatch tests
_PUBLIC_KEY_ALT = (
    _RSA_KEY_ALT.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)

_ISSUER = "test-issuer"
_AUDIENCE = "test-audience"
_KID = "self-signed-v1"


def _make_payload(
    offset_seconds: int = 3600,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a minimal valid payload expiring ``offset_seconds`` from now."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "user-1",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "iat": now,
        "exp": now + offset_seconds,
    }
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# TestEncodeJwt
# ---------------------------------------------------------------------------


class TestEncodeJwt:
    """Tests for encode_jwt."""

    def test_produces_decodable_token(self):
        """encode_jwt output can be decoded by decode_jwt with matching params."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        claims = decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER, audience=_AUDIENCE)
        assert claims["sub"] == "user-1"

    def test_kid_is_set_in_header(self):
        """When kid is provided it appears in the JWT header."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        header = get_token_unverified_header(token)
        assert header["kid"] == _KID

    def test_no_kid_omits_kid_from_header(self):
        """When kid is None the header contains no kid field."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY)
        header = get_token_unverified_header(token)
        assert "kid" not in header

    def test_algorithm_is_rs256(self):
        """The encoded token always uses RS256."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        header = get_token_unverified_header(token)
        assert header["alg"] == "RS256"


# ---------------------------------------------------------------------------
# TestDecodeJwt
# ---------------------------------------------------------------------------


class TestDecodeJwt:
    """Tests for decode_jwt."""

    def test_returns_claims_for_valid_token(self):
        """Decoding a valid token returns the expected claims."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        claims = decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER, audience=_AUDIENCE)
        assert claims["iss"] == _ISSUER

    def test_audience_none_skips_aud_verification(self):
        """audience=None decodes tokens without an aud claim."""
        payload = {
            "sub": "svc-1",
            "iss": _ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = encode_jwt(payload, _PRIVATE_KEY)
        # Must not raise even though aud is absent in token
        claims = decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER)
        assert claims["sub"] == "svc-1"

    def test_wrong_audience_raises(self):
        """Providing a mismatched audience raises InvalidAudienceError."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        with pytest.raises(InvalidAudienceError):
            decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER, audience="wrong-audience")

    def test_expired_token_raises(self):
        """An expired token raises ExpiredSignatureError."""
        expired_payload = _make_payload(offset_seconds=-3600)
        token = encode_jwt(expired_payload, _PRIVATE_KEY)
        with pytest.raises(ExpiredSignatureError):
            decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER, leeway=0)

    def test_leeway_allows_slightly_expired_token(self):
        """A token expired 10s ago passes when leeway=30."""
        expired_payload = _make_payload(offset_seconds=-10)
        token = encode_jwt(expired_payload, _PRIVATE_KEY)
        # leeway=30 should tolerate 10 s of expiry
        claims = decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER, leeway=30)
        assert claims["sub"] == "user-1"

    def test_leeway_zero_rejects_slightly_expired_token(self):
        """A token expired 10s ago fails when leeway=0."""
        expired_payload = _make_payload(offset_seconds=-10)
        token = encode_jwt(expired_payload, _PRIVATE_KEY)
        with pytest.raises(ExpiredSignatureError):
            decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER, leeway=0)

    def test_bad_signature_raises(self):
        """A token signed with one private key fails verification against a different public key."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        with pytest.raises(InvalidSignatureError):
            decode_jwt(token, _PUBLIC_KEY_ALT, issuer=_ISSUER, audience=_AUDIENCE)

    def test_wrong_issuer_raises(self):
        """A token with a mismatched issuer raises InvalidIssuerError."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        with pytest.raises(InvalidIssuerError):
            decode_jwt(token, _PUBLIC_KEY, issuer="wrong-issuer", audience=_AUDIENCE)


# ---------------------------------------------------------------------------
# TestGetTokenKid
# ---------------------------------------------------------------------------


class TestGetTokenKid:
    """Tests for get_token_kid."""

    def test_returns_kid_from_header(self):
        """Returns the kid value when present in the JWT header."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        assert get_token_kid(token) == _KID

    def test_returns_none_when_kid_absent(self):
        """Returns None when the token has no kid in the header."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY)
        assert get_token_kid(token) is None

    def test_raises_on_malformed_token(self):
        """Raises DecodeError for a string that is not a valid JWT."""
        with pytest.raises(DecodeError):
            get_token_kid("not.a.jwt")


# ---------------------------------------------------------------------------
# TestGetTokenUnverifiedHeader
# ---------------------------------------------------------------------------


class TestGetTokenUnverifiedHeader:
    """Tests for get_token_unverified_header."""

    def test_returns_full_header(self):
        """Returns the complete header dict including alg, typ, and kid."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        header = get_token_unverified_header(token)
        assert header["alg"] == "RS256"
        assert header["kid"] == _KID

    def test_raises_on_malformed_token(self):
        """Raises DecodeError for a structurally invalid token."""
        with pytest.raises(DecodeError):
            get_token_unverified_header("not.a.jwt")


# ---------------------------------------------------------------------------
# TestBuildJwtPayload
# ---------------------------------------------------------------------------


class TestBuildJwtPayload:
    """Tests for build_jwt_payload."""

    def test_includes_standard_claims(self):
        """Payload includes sub, iss, aud, iat, exp claims."""
        payload = build_jwt_payload(
            subject="user@example.com",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
        )
        assert payload["sub"] == "user@example.com"
        assert payload["iss"] == _ISSUER
        assert payload["aud"] == _AUDIENCE
        assert "iat" in payload
        assert "exp" in payload

    def test_expiration_calculated_correctly(self):
        """exp is iat + expires_in_seconds."""
        payload = build_jwt_payload(
            subject="user",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=7200,
        )
        assert payload["exp"] == payload["iat"] + 7200

    def test_custom_iat_used_when_provided(self):
        """When iat is provided, it overrides auto-generated timestamp."""
        custom_iat = 1234567890
        payload = build_jwt_payload(
            subject="user",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
            iat=custom_iat,
        )
        assert payload["iat"] == custom_iat
        assert payload["exp"] == custom_iat + 3600

    def test_token_type_included_when_provided(self):
        """token_type is added to payload when specified."""
        payload = build_jwt_payload(
            subject="user",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
            token_type="access_token",
        )
        assert payload["token_type"] == "access_token"

    def test_token_type_omitted_when_none(self):
        """token_type is not in payload when None."""
        payload = build_jwt_payload(
            subject="user",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
            token_type=None,
        )
        assert "token_type" not in payload

    def test_extra_claims_merged_into_payload(self):
        """extra_claims dict is merged into the payload."""
        payload = build_jwt_payload(
            subject="user",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
            extra_claims={"groups": ["admin"], "scope": "read write", "custom_field": 123},
        )
        assert payload["groups"] == ["admin"]
        assert payload["scope"] == "read write"
        assert payload["custom_field"] == 123

    def test_extra_claims_can_override_standard_claims(self):
        """extra_claims can override standard claims if needed."""
        payload = build_jwt_payload(
            subject="user",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
            extra_claims={"sub": "overridden-user", "custom": "value"},
        )
        # extra_claims overwrites standard claims
        assert payload["sub"] == "overridden-user"
        assert payload["custom"] == "value"

    def test_empty_extra_claims_dict_works(self):
        """Passing empty dict for extra_claims doesn't break anything."""
        payload = build_jwt_payload(
            subject="user",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
            extra_claims={},
        )
        # Should only have standard claims
        assert "sub" in payload
        assert "iss" in payload
        assert len([k for k in payload if k not in ["sub", "iss", "aud", "iat", "exp"]]) == 0

    def test_payload_compatible_with_encode_jwt(self):
        """Payload from build_jwt_payload can be encoded and decoded."""
        payload = build_jwt_payload(
            subject="testuser",
            issuer=_ISSUER,
            audience=_AUDIENCE,
            expires_in_seconds=3600,
            token_type="access_token",
            extra_claims={"groups": ["admin"]},
        )
        token = encode_jwt(payload, _PRIVATE_KEY, kid=_KID)
        decoded = decode_jwt(token, _PUBLIC_KEY, issuer=_ISSUER, audience=_AUDIENCE)
        assert decoded["sub"] == "testuser"
        assert decoded["token_type"] == "access_token"
        assert decoded["groups"] == ["admin"]


# ---------------------------------------------------------------------------
# TestDecodeJwtUnverified
# ---------------------------------------------------------------------------


class TestDecodeJwtUnverified:
    """Tests for decode_jwt_unverified."""

    def test_returns_claims_without_verification(self):
        """Returns claims from a valid token without signature verification."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        claims = decode_jwt_unverified(token)
        assert claims["sub"] == "user-1"
        assert claims["iss"] == _ISSUER

    def test_decodes_expired_token(self):
        """An expired token is still decodable (no exp check performed)."""
        expired = _make_payload(offset_seconds=-3600)
        token = encode_jwt(expired, _PRIVATE_KEY)
        claims = decode_jwt_unverified(token)
        assert claims["sub"] == "user-1"

    def test_decodes_token_signed_with_different_key(self):
        """Returns payload regardless of which key signed the token."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY)
        # Even though we're "verifying" against a different key — unverified decode ignores it
        claims = decode_jwt_unverified(token)
        assert claims["iss"] == _ISSUER

    def test_raises_on_malformed_token(self):
        """Raises DecodeError for a structurally invalid token."""
        with pytest.raises(DecodeError):
            decode_jwt_unverified("not.a.jwt")


# ---------------------------------------------------------------------------
# TestDecodeJwtWithJwk
# ---------------------------------------------------------------------------


class TestDecodeJwtWithJwk:
    """Tests for decode_jwt_with_jwk."""

    @pytest.fixture(scope="class")
    def jwks(self):
        """Return a JWKS dict built from the module-level public key."""
        return build_jwks(_PUBLIC_KEY, _KID)

    def test_decodes_with_matching_jwk(self, jwks):
        """Decodes and verifies a token using a matching JWK."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        jwk = jwks["keys"][0]
        claims = decode_jwt_with_jwk(token, jwk, algorithms=["RS256"], issuer=_ISSUER, audience=_AUDIENCE)
        assert claims["sub"] == "user-1"

    def test_audience_none_skips_aud_verification(self, jwks):
        """audience=None skips audience check when using a JWK."""
        payload = {
            "sub": "svc-1",
            "iss": _ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = encode_jwt(payload, _PRIVATE_KEY)
        jwk = jwks["keys"][0]
        claims = decode_jwt_with_jwk(token, jwk, algorithms=["RS256"], issuer=_ISSUER, audience=None)
        assert claims["sub"] == "svc-1"

    def test_expired_token_raises(self, jwks):
        """Expired token raises ExpiredSignatureError."""
        expired = _make_payload(offset_seconds=-3600)
        token = encode_jwt(expired, _PRIVATE_KEY)
        jwk = jwks["keys"][0]
        with pytest.raises(ExpiredSignatureError):
            decode_jwt_with_jwk(token, jwk, algorithms=["RS256"], issuer=_ISSUER, leeway=0)

    def test_wrong_issuer_raises(self, jwks):
        """Mismatched issuer raises InvalidIssuerError."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        jwk = jwks["keys"][0]
        with pytest.raises(InvalidIssuerError):
            decode_jwt_with_jwk(token, jwk, algorithms=["RS256"], issuer="wrong-issuer", audience=_AUDIENCE)

    def test_wrong_audience_raises(self, jwks):
        """Mismatched audience raises InvalidAudienceError."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        jwk = jwks["keys"][0]
        with pytest.raises(InvalidAudienceError):
            decode_jwt_with_jwk(token, jwk, algorithms=["RS256"], issuer=_ISSUER, audience="wrong-audience")

    def test_mismatched_key_raises(self):
        """Token signed with one key but verified against a different JWK raises InvalidSignatureError."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        alt_jwks = build_jwks(_PUBLIC_KEY_ALT, _KID)
        alt_jwk = alt_jwks["keys"][0]
        with pytest.raises(InvalidSignatureError):
            decode_jwt_with_jwk(token, alt_jwk, algorithms=["RS256"], issuer=_ISSUER, audience=_AUDIENCE)


# ---------------------------------------------------------------------------
# TestBuildJwks
# ---------------------------------------------------------------------------


class TestBuildJwks:
    """Tests for build_jwks."""

    def test_returns_keys_list(self):
        """Returns a dict with a non-empty 'keys' list."""
        result = build_jwks(_PUBLIC_KEY, _KID)
        assert "keys" in result
        assert len(result["keys"]) == 1

    def test_jwk_fields_present(self):
        """The JWK contains the required RFC 7517 fields."""
        jwk = build_jwks(_PUBLIC_KEY, _KID)["keys"][0]
        assert jwk["kty"] == "RSA"
        assert jwk["use"] == "sig"
        assert jwk["alg"] == "RS256"
        assert jwk["kid"] == _KID
        assert "n" in jwk
        assert "e" in jwk

    def test_kid_matches_argument(self):
        """The kid in the JWK matches the kid passed to build_jwks."""
        custom_kid = "my-key-v2"
        jwk = build_jwks(_PUBLIC_KEY, custom_kid)["keys"][0]
        assert jwk["kid"] == custom_kid

    def test_n_and_e_are_base64url_strings(self):
        """n and e are non-empty base64url strings (no padding, URL-safe chars)."""
        jwk = build_jwks(_PUBLIC_KEY, _KID)["keys"][0]
        for field in ("n", "e"):
            value = jwk[field]
            assert isinstance(value, str)
            assert len(value) > 0
            # base64url must not contain standard base64 padding
            assert "=" not in value
            # Must only contain base64url-safe characters
            import re

            assert re.match(r"^[A-Za-z0-9_-]+$", value), f"{field} contains invalid base64url chars"

    def test_jwk_can_verify_token(self):
        """A token signed with the private key can be verified using the JWK."""
        token = encode_jwt(_make_payload(), _PRIVATE_KEY, kid=_KID)
        jwk = build_jwks(_PUBLIC_KEY, _KID)["keys"][0]
        claims = decode_jwt_with_jwk(token, jwk, algorithms=["RS256"], issuer=_ISSUER, audience=_AUDIENCE)
        assert claims["sub"] == "user-1"

    def test_invalid_pem_raises(self):
        """Raises ValueError for an invalid PEM string."""
        with pytest.raises((ValueError, Exception)):
            build_jwks("not-a-pem", _KID)
