"""
Cryptographic utilities for encrypting/decrypting sensitive data.

This module provides AES-CBC encryption compatible with the TypeScript
encryption implementation used elsewhere in the system.

TypeScript equivalent:
- Algorithm: AES-CBC
- Key derivation: settings.encryption_key - guaranteed to be valid only app starts up successfully
- IV: Random 16 bytes per encryption
- Format: hex(iv):hex(ciphertext)
"""

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from registry_pkgs.core.jwt_utils import (
    ExpiredSignatureError,
    InvalidTokenError,
    build_jwt_payload,
    decode_jwt,
    encode_jwt,
    get_token_kid,
)

from ..core.config import settings

logger = logging.getLogger(__name__)

# Token expiration defaults
ACCESS_TOKEN_EXPIRES_HOURS = 24  # 1 day
REFRESH_TOKEN_EXPIRES_DAYS = 7  # 7 days


# Algorithm constants
ALGORITHM = "AES-CBC"
IV_LENGTH = 16  # 128 bits

# Encryption format validation
# Encrypted values have format: hex(iv):hex(ciphertext)
# IV is always 16 bytes = 32 hex characters
ENCRYPTED_VALUE_PATTERN = re.compile(r"^[0-9a-f]{32}:")


def is_encrypted(value: str) -> bool:
    """
    Check if a value is already encrypted using strict pattern matching.

    Encrypted values have the format: hex(iv):hex(ciphertext)
    where IV is always 16 bytes (32 hex characters).

    Args:
        value: String value to check

    Returns:
        True if value matches encrypted format, False otherwise
    """
    if not value or not isinstance(value, str):
        return False
    return bool(ENCRYPTED_VALUE_PATTERN.match(value))


def generate_service_jwt(
    user_id: str | None = None,
    username: str | None = None,
    scopes: list[str] | None = None,
    for_agentcore_runtime: bool = False,
    expires_in_seconds: int = 300,
) -> str:
    """
    Generate service JWT for MCP server authentication.

    Supports two modes:
    1. Internal service JWT: Registry -> MCP server requests with user context
    2. AgentCore Runtime JWT: Simple JWT for AWS AgentCore Runtime authentication

    Args:
        user_id: User ID to include in JWT (required for internal mode)
        username: Optional username/email (for internal mode)
        scopes: Optional list of scopes (for internal mode)
        for_agentcore_runtime: If True, generates simplified JWT for AgentCore Runtime
        expires_in_seconds: Token expiration in seconds (default: 300 = 5 minutes)

    Returns:
        JWT token string (without Bearer prefix)
    """
    now = int(datetime.now(UTC).timestamp())

    if for_agentcore_runtime:
        # AgentCore Runtime mode: AWS only validates iss, aud, and signature
        # Generate minimal JWT with only required claims
        payload = build_jwt_payload(
            subject=settings.registry_app_name,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            expires_in_seconds=expires_in_seconds,
            iat=now,
            extra_claims=None,
        )
    else:
        # Internal service mode: Include user context
        if not user_id:
            raise ValueError("user_id is required for internal service JWT")

        # Build extra claims
        extra_claims = {
            "user_id": user_id,
            "jti": f"registry-{now}",
            "client_id": settings.registry_app_name,
            "token_type": "service",
        }

        # Add optional scopes
        if scopes:
            extra_claims["scopes"] = scopes

        # Build JWT payload using centralized helper
        payload = build_jwt_payload(
            subject=username or user_id,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            expires_in_seconds=expires_in_seconds,
            iat=now,
            extra_claims=extra_claims,
        )

    # Sign with the registry JWT private key
    token = encode_jwt(payload, settings.jwt_private_key)

    return token


def encrypt_value(plaintext: str) -> str:
    """
    Encrypts a value using AES-CBC with a random IV.

    This implementation is compatible with the TypeScript encryptV2 function:
    - Uses AES-CBC encryption (matching Web Crypto API)
    - Generates a random 16-byte IV for each encryption
    - Returns format: hex(iv):hex(ciphertext)
    - NO padding (Web Crypto API handles this automatically)

    Args:
        plaintext: The plaintext string to encrypt

    Returns:
        str: Encrypted string in format "iv_hex:ciphertext_hex"

    Raises:
        ValueError: If CREDS_KEY is not configured
        Exception: If encryption fails
    """
    if not plaintext:
        return plaintext

    try:
        # Generate random IV
        gen_iv = os.urandom(IV_LENGTH)

        # Encode plaintext
        plaintext_bytes = plaintext.encode("utf-8")

        # Pad to 16-byte boundary (AES block size)
        block_size = 16
        padding_length = block_size - (len(plaintext_bytes) % block_size)
        padded_data = plaintext_bytes + bytes([padding_length] * padding_length)

        # Create cipher
        cipher = Cipher(algorithms.AES(settings.encryption_key), modes.CBC(gen_iv), backend=default_backend())
        encryptor = cipher.encryptor()

        # Encrypt
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        # Return as hex(iv):hex(ciphertext)
        return gen_iv.hex() + ":" + ciphertext.hex()

    except Exception as e:
        logger.error(f"Encryption failed: {e}", exc_info=True)
        raise Exception(f"Failed to encrypt value: {e}")


def decrypt_value(encrypted_value: str) -> str:
    """
    Decrypts an encrypted value using AES-CBC.

    This implementation is compatible with the TypeScript decryptV2 function:
    - Expects format: hex(iv):hex(ciphertext)
    - Uses AES-CBC decryption (matching Web Crypto API)
    - Returns original plaintext

    If the value doesn't contain a colon separator, it's assumed to be
    already decrypted and returned as-is (for backward compatibility).

    Args:
        encrypted_value: The encrypted string in format "iv_hex:ciphertext_hex"

    Returns:
        str: Decrypted plaintext string

    Raises:
        ValueError: If CREDS_KEY is not configured or format is invalid
        Exception: If decryption fails
    """
    if not encrypted_value:
        return encrypted_value

    # Check if value is encrypted (contains colon separator)
    parts = encrypted_value.split(":")
    if len(parts) == 1:
        # Not encrypted, return as-is (matching TS: if (parts.length === 1) return parts[0])
        return parts[0]

    try:
        # Split IV and ciphertext (matching TS logic)
        gen_iv = bytes.fromhex(parts[0])
        encrypted = ":".join(parts[1:])

        # Convert ciphertext from hex
        ciphertext = bytes.fromhex(encrypted)

        # Validate IV length
        if len(gen_iv) != IV_LENGTH:
            raise ValueError(f"Invalid IV length: expected {IV_LENGTH}, got {len(gen_iv)}")

        # Create cipher
        cipher = Cipher(algorithms.AES(settings.encryption_key), modes.CBC(gen_iv), backend=default_backend())
        decryptor = cipher.decryptor()

        # Decrypt
        padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        # Remove padding (standard PKCS#7 unpadding)
        padding_length = padded_plaintext[-1]
        plaintext_bytes = padded_plaintext[:-padding_length]

        # Convert to string
        return plaintext_bytes.decode("utf-8")

    except Exception as e:
        logger.error(f"Decryption failed: {e}", exc_info=True)
        raise Exception(f"Failed to decrypt value: {e}")


def encrypt_auth_fields(config: dict) -> dict:
    """
    Encrypt sensitive authentication fields in server config.

    Handles two authentication patterns:
    1. oauth.client_secret - OAuth client secret
    2. apiKey.key - API key value

    Args:
        config: Server configuration dictionary

    Returns:
        dict: Config with encrypted sensitive fields

    Note:
        If CREDS_KEY is not set, values will be stored as plaintext.
        A warning will be logged in this case.
    """
    if not config:
        return config

    config = config.copy()

    # Check if CREDS_KEY is available
    if not settings.creds_key:
        logger.warning(
            "CREDS_KEY configuration is not set. "
            "Sensitive authentication fields will be stored as PLAINTEXT. "
            "Set CREDS_KEY environment variable to enable encryption of credentials."
        )
        return config

    try:
        # Handle oauth field
        if "oauth" in config and isinstance(config["oauth"], dict):
            oauth = config["oauth"].copy()

            if "client_secret" in oauth:
                # Encrypt OAuth client_secret
                client_secret = oauth["client_secret"]
                if client_secret and not is_encrypted(str(client_secret)):
                    # Only encrypt if not already encrypted
                    try:
                        oauth["client_secret"] = encrypt_value(str(client_secret))
                        config["oauth"] = oauth
                        logger.debug("Encrypted oauth.client_secret")
                    except Exception as encrypt_error:
                        logger.error(f"Failed to encrypt oauth.client_secret: {encrypt_error}")
                        # Keep plaintext value

        # Handle apiKey field
        if "apiKey" in config and isinstance(config["apiKey"], dict):
            api_key = config["apiKey"].copy()

            if "key" in api_key:
                key_value = api_key["key"]
                if key_value and ":" not in str(key_value):
                    # Only encrypt if not already encrypted
                    try:
                        api_key["key"] = encrypt_value(str(key_value))
                        config["apiKey"] = api_key
                        logger.debug("Encrypted apiKey.key")
                    except Exception as encrypt_error:
                        logger.error(f"Failed to encrypt apiKey.key: {encrypt_error}")
                        # Keep plaintext value

    except Exception as e:
        logger.error(f"Failed to encrypt auth fields: {e}", exc_info=True)
        # Return original config if encryption fails
        return config

    return config


def decrypt_auth_fields(config: dict) -> dict:
    """
    Decrypt sensitive authentication fields in server config.

    Handles two authentication patterns:
    1. oauth.client_secret - OAuth client secret
    2. apiKey.key - API key value

    Args:
        config: Server configuration dictionary with encrypted fields

    Returns:
        dict: Config with decrypted sensitive fields

    Note:
        If CREDS_KEY is not set, encrypted values will be returned as-is (still encrypted).
        This prevents the API from crashing when CREDS_KEY is not configured.
    """
    if not config:
        return config

    config = config.copy()

    # Check if CREDS_KEY is available
    if not settings.creds_key:
        logger.warning(
            "CREDS_KEY configuration is not set. "
            "Encrypted authentication fields will be returned as-is (still encrypted). "
            "Set CREDS_KEY environment variable to decrypt sensitive credentials."
        )
        return config

    try:
        # Handle oauth field
        if "oauth" in config and isinstance(config["oauth"], dict):
            oauth = config["oauth"].copy()

            if "client_secret" in oauth:
                # Decrypt OAuth client_secret
                client_secret = oauth["client_secret"]
                if client_secret:
                    try:
                        oauth["client_secret"] = decrypt_value(str(client_secret))
                        config["oauth"] = oauth
                        logger.debug("Decrypted oauth.client_secret")
                    except Exception as decrypt_error:
                        logger.warning(f"Failed to decrypt oauth.client_secret: {decrypt_error}")
                        # Keep encrypted value

        # Handle apiKey field
        if "apiKey" in config and isinstance(config["apiKey"], dict):
            api_key = config["apiKey"].copy()

            if "key" in api_key:
                key_value = api_key["key"]
                if key_value:
                    try:
                        api_key["key"] = decrypt_value(str(key_value))
                        config["apiKey"] = api_key
                        logger.debug("Decrypted apiKey.key")
                    except Exception as decrypt_error:
                        logger.warning(f"Failed to decrypt apiKey.key: {decrypt_error}")
                        # Keep encrypted value

    except Exception as e:
        logger.error(f"Failed to decrypt auth fields: {e}", exc_info=True)
        # Return original config if decryption fails
        return config

    return config


def generate_access_token(
    user_id: str,
    username: str,
    email: str,
    groups: list,
    scopes: list,
    role: str,
    auth_method: str,
    provider: str,
    idp_id: str | None = None,
    expires_hours: int = ACCESS_TOKEN_EXPIRES_HOURS,
    iat: int | None = None,
    exp: int | None = None,
) -> str:
    """
    Generate a JWT access token for authenticated user.

    Args:
        user_id: User's database ID
        username: Username
        email: User's email
        groups: List of user groups
        scopes: List of permission scopes
        role: User role
        auth_method: Authentication method (oauth2, traditional, etc.)
        provider: Auth provider (entra, keycloak, local, etc.)
        idp_id: Identity provider user ID (optional)
        expires_hours: Token expiration in hours (default: 24)
        iat: Issued at timestamp (optional, honors OAuth token iat)
        exp: Expiration timestamp (optional, honors OAuth token exp)

    Returns:
        JWT token string
    """
    # If both iat and exp are provided (from OAuth), compute expires_in_seconds
    if iat is not None and exp is not None:
        expires_in_seconds = exp - iat
    else:
        expires_in_seconds = expires_hours * 3600
        iat = None  # Let build_jwt_payload generate iat

    # Build extra claims
    extra_claims = {
        "user_id": user_id,
        "email": email,
        "groups": groups,
        "scope": " ".join(scopes) if isinstance(scopes, list) else scopes,
        "role": role,
        "auth_method": auth_method,
        "provider": provider,
    }

    # Add optional claims
    if idp_id:
        extra_claims["idp_id"] = idp_id

    # Build JWT payload using centralized helper
    payload = build_jwt_payload(
        subject=username,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        expires_in_seconds=expires_in_seconds,
        token_type="access_token",
        iat=iat,
        extra_claims=extra_claims,
    )

    # Generate JWT
    token = encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)

    logger.debug(f"Generated access token for user {username}, expires in {expires_hours}h")
    return token


def generate_refresh_token(
    user_id: str,
    username: str,
    auth_method: str,
    provider: str,
    groups: list,
    scopes: list,
    role: str,
    email: str,
    expires_days: int = REFRESH_TOKEN_EXPIRES_DAYS,
) -> str:
    """
    Generate a JWT refresh token.

    Refresh tokens now include groups and scopes to enable token refresh without re-authentication.
    This is especially important for OAuth2 users who cannot re-authenticate automatically.

    Args:
        user_id: User's database ID
        username: Username
        auth_method: Authentication method
        provider: Auth provider
        groups: List of user groups
        scopes: List of permission scopes
        role: User role
        email: User's email
        expires_days: Token expiration in days (default: 7)

    Returns:
        JWT token string
    """
    expires_in_seconds = expires_days * 86400  # Convert days to seconds

    # Build extra claims - include groups/scopes for token refresh
    extra_claims = {
        "user_id": user_id,
        "auth_method": auth_method,
        "provider": provider,
        "groups": groups,
        "scope": " ".join(scopes) if isinstance(scopes, list) else scopes,
        "role": role,
        "email": email,
    }

    # Build JWT payload using centralized helper
    payload = build_jwt_payload(
        subject=username,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        expires_in_seconds=expires_in_seconds,
        token_type="refresh_token",
        extra_claims=extra_claims,
    )

    token = encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)

    logger.debug(f"Generated refresh token for user {username}, expires in {expires_days} days")
    return token


def verify_access_token(token: str) -> dict[str, Any] | None:
    """
    Verify and decode an access token.

    Args:
        token: JWT token string

    Returns:
        Decoded token claims if valid, None otherwise
    """
    try:
        # Verify kid in header
        kid = get_token_kid(token)

        if kid != settings.jwt_self_signed_kid:
            logger.debug(f"Invalid kid in token: {kid}")
            return None

        # Decode and verify token
        claims = decode_jwt(
            token,
            settings.jwt_public_key,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=30,
        )

        # Verify token type
        if claims.get("token_type") != "access_token":
            logger.warning(f"Wrong token type: {claims.get('token_type')}")
            return None

        logger.debug(f"Access token verified for user: {claims.get('sub')}")
        return claims

    except ExpiredSignatureError:
        logger.debug("Access token expired")
        return None
    except InvalidTokenError as e:
        logger.debug(f"Invalid access token: {e}")
        return None
    except Exception as e:
        logger.error(f"Error verifying access token: {e}")
        return None


def verify_refresh_token(token: str) -> dict[str, Any] | None:
    """
    Verify and decode a refresh token.

    Args:
        token: JWT token string

    Returns:
        Decoded token claims if valid, None otherwise
    """
    try:
        # Verify kid in header
        kid = get_token_kid(token)

        if kid != settings.jwt_self_signed_kid:
            logger.debug(f"Invalid kid in refresh token: {kid}")
            return None

        # Decode and verify token
        claims = decode_jwt(
            token,
            settings.jwt_public_key,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=30,
        )

        # Verify token type
        if claims.get("token_type") != "refresh_token":
            logger.warning(f"Wrong token type in refresh: {claims.get('token_type')}")
            return None

        logger.debug(f"Refresh token verified for user: {claims.get('sub')}")
        return claims

    except ExpiredSignatureError:
        logger.debug("Refresh token expired")
        return None
    except InvalidTokenError as e:
        logger.debug(f"Invalid refresh token: {e}")
        return None
    except Exception as e:
        logger.error(f"Error verifying refresh token: {e}")
        return None


def generate_token_pair(
    user_id: str = None,
    username: str = None,
    email: str = None,
    groups: list = None,
    scopes: list = None,
    role: str = None,
    auth_method: str = None,
    provider: str = None,
    idp_id: str | None = None,
    user_info: dict[str, Any] | None = None,
    iat: int | None = None,
    exp: int | None = None,
) -> tuple[str, str]:
    """
    Generate both access and refresh tokens.

    Can accept either individual parameters or a user_info dict.
    If user_info is provided, it takes precedence over individual parameters.

    Args:
        user_id: User's database ID
        username: Username
        email: User's email
        groups: List of user groups
        scopes: List of permission scopes
        role: User role
        auth_method: Authentication method
        provider: Auth provider
        idp_id: Identity provider user ID (optional)
        user_info: Dict containing user info (takes precedence if provided)
        iat: Issued at timestamp (optional, honors OAuth token iat)
        exp: Expiration timestamp (optional, honors OAuth token exp)

    Returns:
        Tuple of (access_token, refresh_token)
    """
    # Use user_info dict if provided, otherwise use individual parameters
    if user_info:
        user_id = user_info.get("user_id", user_id)
        username = user_info.get("username", username)
        email = user_info.get("email", email)
        groups = user_info.get("groups", groups or [])
        scopes = user_info.get("scopes", scopes or [])
        role = user_info.get("role", role)
        auth_method = user_info.get("auth_method", auth_method)
        provider = user_info.get("provider", provider)
        idp_id = user_info.get("idp_id", idp_id)
        iat = user_info.get("iat", iat)
        exp = user_info.get("exp", exp)

    access_token = generate_access_token(
        user_id=user_id,
        username=username,
        email=email,
        groups=groups,
        scopes=scopes,
        role=role,
        auth_method=auth_method,
        provider=provider,
        idp_id=idp_id,
        iat=iat,
        exp=exp,
    )

    refresh_token = generate_refresh_token(
        user_id=user_id,
        username=username,
        auth_method=auth_method,
        provider=provider,
        groups=groups,
        scopes=scopes,
        role=role,
        email=email,
    )

    return access_token, refresh_token
