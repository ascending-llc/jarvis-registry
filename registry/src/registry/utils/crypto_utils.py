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
from datetime import UTC, datetime
from typing import Any

from registry_pkgs.core.crypto_utils import decrypt_auth_fields as _decrypt_auth_fields
from registry_pkgs.core.crypto_utils import decrypt_value as _decrypt_value
from registry_pkgs.core.crypto_utils import encrypt_auth_fields as _encrypt_auth_fields
from registry_pkgs.core.crypto_utils import encrypt_value as _encrypt_value
from registry_pkgs.core.jwt_tokens import mint_crud_session_token, verify_crud_session_token
from registry_pkgs.core.jwt_utils import (
    ExpiredSignatureError,
    InvalidTokenError,
)

from ..core.config import settings

logger = logging.getLogger(__name__)

# Token expiration defaults
ACCESS_TOKEN_EXPIRES_HOURS = 24  # 1 day
REFRESH_TOKEN_EXPIRES_DAYS = 2  # 48 hours
REFRESH_TOKEN_EXPIRES_SECONDS = REFRESH_TOKEN_EXPIRES_DAYS * 86400
ABSOLUTE_SESSION_EXPIRES_DAYS = 14
ABSOLUTE_SESSION_EXPIRES_SECONDS = ABSOLUTE_SESSION_EXPIRES_DAYS * 86400


def encrypt_value(plaintext: str) -> str:
    """Encrypt a value with the registry encryption key."""
    return _encrypt_value(plaintext, encryption_key=settings.encryption_key)


def decrypt_value(encrypted_value: str) -> str:
    """Decrypt a value with the registry encryption key."""
    return _decrypt_value(encrypted_value, encryption_key=settings.encryption_key)


def _auth_fields_key() -> bytes | None:
    """Registry encryption key for auth-field crypto, or None when CREDS_KEY is unset."""
    return settings.encryption_key if settings.creds_key else None


def encrypt_auth_fields(config: dict) -> dict:
    """Encrypt sensitive auth fields with the registry encryption key."""
    return _encrypt_auth_fields(config, encryption_key=_auth_fields_key())


def decrypt_auth_fields(config: dict) -> dict:
    """Decrypt sensitive auth fields with the registry encryption key."""
    return _decrypt_auth_fields(config, encryption_key=_auth_fields_key())


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

    # CRUD-session (cookie) class: audience, client_id and token_class are set by the layer.
    token = mint_crud_session_token(
        settings.jwt_token_config,
        subject=username,
        token_type="access_token",
        expires_in_seconds=expires_in_seconds,
        iat=iat,
        extra_claims=extra_claims,
    )

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
    session_started_at: int | None = None,
) -> str:
    """
    Generate a JWT refresh token.

    Refresh tokens now include groups and scopes to enable token refresh without re-authentication.
    They are stateless JWTs: reissuing a token renews the browser cookie but does not revoke
    the previous token before its own expiration.
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
        expires_days: Token expiration in days (default: 2)
        session_started_at: Unix timestamp of original login; stamped once and carried forward
            through every rotation to enforce the absolute 14-day session cap.

    Returns:
        JWT token string
    """
    expires_in_seconds = expires_days * 86400  # Convert days to seconds

    if session_started_at is None:
        session_started_at = int(datetime.now(UTC).timestamp())

    # Build extra claims - include groups/scopes for token refresh
    extra_claims = {
        "user_id": user_id,
        "auth_method": auth_method,
        "provider": provider,
        "groups": groups,
        "scope": " ".join(scopes) if isinstance(scopes, list) else scopes,
        "role": role,
        "email": email,
        "session_started_at": session_started_at,
    }

    # CRUD-session (cookie) class: audience, client_id and token_class are set by the layer.
    token = mint_crud_session_token(
        settings.jwt_token_config,
        subject=username,
        token_type="refresh_token",
        expires_in_seconds=expires_in_seconds,
        extra_claims=extra_claims,
    )

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
        claims = verify_crud_session_token(settings.jwt_token_config, token, expected_token_type="access_token")
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
        claims = verify_crud_session_token(settings.jwt_token_config, token, expected_token_type="refresh_token")
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
    user_id: str | None = None,
    username: str | None = None,
    email: str | None = None,
    groups: list | None = None,
    scopes: list | None = None,
    role: str | None = None,
    auth_method: str | None = None,
    provider: str | None = None,
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
