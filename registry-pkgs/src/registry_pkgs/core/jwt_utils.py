"""JWT encoding and decoding utilities for MCP Gateway Registry."""

import logging
from datetime import UTC, datetime
from typing import Any

import jwt
from jwt import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
)
from jwt.api_jwk import PyJWK

logger = logging.getLogger(__name__)

_ALGORITHM = "RS256"
_DEFAULT_LEEWAY = 30  # seconds — clock skew tolerance

__all__ = [
    "build_jwt_payload",
    "build_jwks",
    "decode_jwt",
    "decode_jwt_unverified",
    "decode_jwt_with_jwk",
    "encode_jwt",
    "get_token_kid",
    "get_token_unverified_header",
    # Re-exported pyjwt exceptions so callers outside registry-pkgs
    # never need to import pyjwt directly.
    "DecodeError",
    "ExpiredSignatureError",
    "InvalidAudienceError",
    "InvalidIssuerError",
    "InvalidSignatureError",
    "InvalidTokenError",
]


def build_jwt_payload(
    subject: str,
    issuer: str,
    audience: str,
    expires_in_seconds: int,
    token_type: str | None = None,
    iat: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a standardized JWT payload with common claims.

    Constructs a JWT payload dict with standard claims (sub, iss, aud, iat, exp)
    and optional extra claims. Centralizes JWT claim structure across services.

    Args:
        subject: Subject claim (typically username or user ID).
        issuer: Issuer claim (typically service name).
        audience: Audience claim (typically target service).
        expires_in_seconds: Token expiration time from now in seconds.
        token_type: Optional token type (e.g., "access_token", "refresh_token").
            Added as the ``token_type`` claim when provided; omitted when None.
        iat: Optional issued-at timestamp (Unix seconds). When None, the current
            UTC time is used.
        extra_claims: Optional dict of additional claims to merge into the
            payload. Keys in this dict override standard claims if they collide.

    Returns:
        dict[str, Any]: JWT payload containing at minimum ``sub``, ``iss``,
            ``aud``, ``iat``, and ``exp``. Any ``token_type`` and
            ``extra_claims`` are merged in on top.

    Raises:
        No exceptions are raised by this function.
    """
    now = iat if iat is not None else int(datetime.now(UTC).timestamp())
    exp = now + expires_in_seconds

    payload: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": exp,
    }

    if token_type is not None:
        payload["token_type"] = token_type

    if extra_claims:
        payload.update(extra_claims)

    return payload


def encode_jwt(
    payload: dict[str, Any],
    private_key_pem: str,
    kid: str | None = None,
) -> str:
    """Encode a JWT using RS256 with an RSA private key.

    Args:
        payload: Claims dict to encode. Must contain at least ``sub``, ``iss``,
            ``aud``, ``iat``, and ``exp``.
        private_key_pem: PEM-encoded RSA private key string used to sign the
            token (RS256 algorithm).
        kid: Key ID added to the JWT header when provided. Omitted from the
            header when None.

    Returns:
        str: Compact serialized JWT string (``header.payload.signature``).

    Raises:
        jwt.exceptions.InvalidKeyError: If ``private_key_pem`` is not a valid
            RSA private key.
    """
    headers: dict[str, str] | None = {"kid": kid} if kid is not None else None
    return jwt.encode(payload, private_key_pem, algorithm=_ALGORITHM, headers=headers)


def decode_jwt(
    token: str,
    public_key_pem: str,
    issuer: str,
    audience: str | None = None,
    leeway: int = _DEFAULT_LEEWAY,
) -> dict[str, Any]:
    """Decode and verify a self-signed JWT using an RSA public key.

    Verifies the RS256 signature, expiry, and issuer. Audience verification is
    optional and controlled by the ``audience`` argument.

    Args:
        token: Compact JWT string to decode and verify.
        public_key_pem: PEM-encoded RSA public key string used to verify the
            RS256 signature.
        issuer: Expected value of the ``iss`` claim. Verification fails if the
            token's issuer does not match.
        audience: Expected value of the ``aud`` claim. When None, audience
            verification is skipped entirely (useful for tokens whose audience
            is a dynamic resource URL).
        leeway: Clock-skew tolerance in seconds applied to ``exp`` and ``iat``
            checks. Defaults to 30.

    Returns:
        dict[str, Any]: Decoded and verified claims dictionary.

    Raises:
        jwt.ExpiredSignatureError: Token's ``exp`` claim is in the past (beyond
            the configured ``leeway``).
        jwt.InvalidIssuerError: Token's ``iss`` claim does not match
            ``issuer``.
        jwt.InvalidAudienceError: Token's ``aud`` claim does not match
            ``audience`` (only raised when ``audience`` is not None).
        jwt.InvalidSignatureError: RS256 signature verification failed —
            token was not signed with the corresponding private key.
        jwt.DecodeError: Token is structurally malformed and cannot be parsed.
        jwt.InvalidTokenError: Catch-all for any other PyJWT validation
            failure.
    """
    verify_aud = audience is not None
    options: dict[str, Any] = {
        "verify_exp": True,
        "verify_iat": True,
        "verify_iss": True,
        "verify_aud": verify_aud,
    }
    decode_kwargs: dict[str, Any] = {
        "algorithms": [_ALGORITHM],
        "issuer": issuer,
        "options": options,
        "leeway": leeway,
    }
    if verify_aud:
        decode_kwargs["audience"] = audience

    return jwt.decode(token, public_key_pem, **decode_kwargs)


def decode_jwt_unverified(token: str) -> dict[str, Any]:
    """Decode a JWT without verifying its signature or any claims.

    Intended for inspecting the payload of externally-issued tokens (e.g. IdP
    tokens received from Keycloak, Cognito, or Entra) where the caller needs
    to extract claims before performing full verification, or where the token's
    signature cannot be verified locally.

    **Security note**: Do NOT use the returned claims to make authorization
    decisions. This function performs no cryptographic verification whatsoever.

    Args:
        token: Compact JWT string to decode (may be signed with any algorithm).

    Returns:
        dict[str, Any]: Raw claims dictionary extracted from the token payload,
            with no guarantee of authenticity.

    Raises:
        jwt.DecodeError: Token is structurally malformed and the payload cannot
            be base64-decoded or JSON-parsed.
    """
    return jwt.decode(token, options={"verify_signature": False})


def decode_jwt_with_jwk(
    token: str,
    jwk_data: dict[str, Any],
    algorithms: list[str],
    issuer: str,
    audience: str | None = None,
    leeway: int = _DEFAULT_LEEWAY,
) -> dict[str, Any]:
    """Decode and verify a JWT using a JSON Web Key (JWK).

    Used to verify tokens issued by external identity providers (Keycloak,
    Cognito, Entra ID) whose public key is obtained from a JWKS endpoint.

    Args:
        token: Compact JWT string to decode and verify.
        jwk_data: A single JWK object as a dict (e.g. one entry from the
            ``"keys"`` array of a JWKS response). Must be a public key
            appropriate for verifying the token's algorithm.
        algorithms: List of algorithm strings that are acceptable (e.g.
            ``["RS256"]``). Passed directly to PyJWT to prevent algorithm
            confusion attacks.
        issuer: Expected value of the ``iss`` claim.
        audience: Expected value of the ``aud`` claim. When None, audience
            verification is skipped.
        leeway: Clock-skew tolerance in seconds. Defaults to 30.

    Returns:
        dict[str, Any]: Decoded and verified claims dictionary.

    Raises:
        jwt.ExpiredSignatureError: Token's ``exp`` claim is in the past (beyond
            ``leeway``).
        jwt.InvalidIssuerError: Token's ``iss`` claim does not match
            ``issuer``.
        jwt.InvalidAudienceError: Token's ``aud`` claim does not match
            ``audience`` (only raised when ``audience`` is not None).
        jwt.InvalidSignatureError: Signature verification failed against the
            provided JWK.
        jwt.DecodeError: Token is structurally malformed or the JWK is invalid.
        jwt.InvalidTokenError: Catch-all for any other PyJWT validation
            failure.
        ValueError: ``jwk_data`` cannot be parsed into a valid PyJWK object.
    """
    public_key = PyJWK(jwk_data).key
    verify_aud = audience is not None
    options: dict[str, Any] = {
        "verify_exp": True,
        "verify_iat": True,
        "verify_iss": True,
        "verify_aud": verify_aud,
    }
    decode_kwargs: dict[str, Any] = {
        "algorithms": algorithms,
        "issuer": issuer,
        "options": options,
        "leeway": leeway,
    }
    if verify_aud:
        decode_kwargs["audience"] = audience

    return jwt.decode(token, public_key, **decode_kwargs)


def get_token_kid(token: str) -> str | None:
    """Return the ``kid`` from the unverified JWT header.

    Args:
        token: Compact JWT string. The header is decoded without signature
            verification.

    Returns:
        str | None: The ``kid`` (key ID) value from the header, or None if
            the header contains no ``kid`` field.

    Raises:
        jwt.DecodeError: The token header cannot be base64-decoded or
            JSON-parsed (i.e. the token is structurally malformed).
    """
    header = jwt.get_unverified_header(token)
    return header.get("kid")


def get_token_unverified_header(token: str) -> dict[str, Any]:
    """Return the full unverified JWT header as a dict.

    Decodes the header segment without verifying the token's signature. Useful
    when callers need header fields beyond ``kid`` (e.g. ``alg``, ``typ``).

    Args:
        token: Compact JWT string. The header is decoded without signature
            verification.

    Returns:
        dict[str, Any]: The decoded JWT header dictionary (e.g.
            ``{"alg": "RS256", "typ": "JWT", "kid": "key-v1"}``).

    Raises:
        jwt.DecodeError: The token header cannot be base64-decoded or
            JSON-parsed (i.e. the token is structurally malformed).
    """
    return jwt.get_unverified_header(token)


def build_jwks(public_key_pem: str, kid: str) -> dict[str, Any]:
    """Build a JSON Web Key Set (JWKS) dict from an RSA public key PEM.

    Constructs a JWKS response body containing a single RSA public key in
    JWK format (RFC 7517), suitable for serving at ``/.well-known/jwks.json``
    so that token consumers can fetch the public key and verify RS256-signed
    JWTs issued by this service.

    Args:
        public_key_pem: PEM-encoded RSA public key string (begins with
            ``-----BEGIN PUBLIC KEY-----`` or
            ``-----BEGIN RSA PUBLIC KEY-----``).
        kid: Key ID string to embed in the JWK's ``kid`` field. Should match
            the ``kid`` used when signing tokens with :func:`encode_jwt`.

    Returns:
        dict[str, Any]: A JWKS dict with shape::

            {
                "keys": [{
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "<kid>",
                    "n":   "<base64url-encoded modulus>",
                    "e":   "<base64url-encoded exponent>"
                }]
            }

    Raises:
        ValueError: ``public_key_pem`` cannot be parsed as an RSA public key.
        TypeError: ``public_key_pem`` is not a string.
    """
    import base64

    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(public_key, RSAPublicKey):
        raise ValueError("public_key_pem must be an RSA public key")

    pub_numbers = public_key.public_numbers()

    def _int_to_base64url(value: int) -> str:
        byte_length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(byte_length, "big")).rstrip(b"=").decode("ascii")

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _int_to_base64url(pub_numbers.n),
                "e": _int_to_base64url(pub_numbers.e),
            }
        ]
    }
