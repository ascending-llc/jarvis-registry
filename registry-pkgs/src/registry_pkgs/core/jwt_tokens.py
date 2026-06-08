import logging
from typing import Any

from .config import JwtTokenConfig
from .jwt_utils import (
    InvalidTokenError,
    build_jwt_payload,
    decode_jwt,
    encode_jwt,
    get_token_kid,
)

logger = logging.getLogger(__name__)

TOKEN_CLASS_CLAIM = "token_class"
TOKEN_CLASS_MANAGED_AGENT = "managed_agent"
TOKEN_CLASS_CRUD_SESSION = "crud_session"

__all__ = [
    "TOKEN_CLASS_CLAIM",
    "TOKEN_CLASS_MANAGED_AGENT",
    "TOKEN_CLASS_CRUD_SESSION",
    "mint_managed_agent_token",
    "mint_crud_session_token",
    "verify_managed_agent_token",
    "verify_crud_session_token",
]


def _merge_class_claims(extra_claims: dict[str, Any] | None, token_class: str, client_id: str) -> dict[str, Any]:
    claims: dict[str, Any] = dict(extra_claims or {})
    claims["client_id"] = client_id
    claims[TOKEN_CLASS_CLAIM] = token_class
    return claims


def mint_managed_agent_token(
    config: JwtTokenConfig,
    *,
    subject: str,
    client_id: str,
    expires_in_seconds: int,
    iat: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a managed-agent (proxy / Bearer) self-signed JWT.

    ``client_id`` is the requesting client's id (DCR-registered client, the literal
    ``"user-generated"`` for token vending, or a device-flow client). It is recorded
    as-is; the proxy verifier later rejects tokens whose ``client_id`` equals the
    registry backend's own id.
    """
    payload = build_jwt_payload(
        subject=subject,
        issuer=config.jwt_issuer,
        audience=config.managed_agents_audience,
        expires_in_seconds=expires_in_seconds,
        iat=iat,
        extra_claims=_merge_class_claims(extra_claims, TOKEN_CLASS_MANAGED_AGENT, client_id),
    )
    return encode_jwt(payload, config.jwt_private_key, kid=config.jwt_self_signed_kid)


def mint_crud_session_token(
    config: JwtTokenConfig,
    *,
    subject: str,
    token_type: str,
    expires_in_seconds: int,
    iat: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a CRUD-session (cookie) self-signed JWT.

    ``client_id`` is fixed to the registry backend's own id — CRUD session tokens are
    always issued by the registry acting as the first-party principal. ``token_type``
    distinguishes ``"access_token"`` from ``"refresh_token"``.
    """
    payload = build_jwt_payload(
        subject=subject,
        issuer=config.jwt_issuer,
        audience=config.crud_services_audience,
        expires_in_seconds=expires_in_seconds,
        token_type=token_type,
        iat=iat,
        extra_claims=_merge_class_claims(extra_claims, TOKEN_CLASS_CRUD_SESSION, config.registry_client_id),
    )
    return encode_jwt(payload, config.jwt_private_key, kid=config.jwt_self_signed_kid)


def _decode_self_signed(config: JwtTokenConfig, token: str, audience: str) -> dict[str, Any]:
    """Reject foreign-kid tokens early, then verify signature + iss + aud."""
    kid = get_token_kid(token)
    if kid != config.jwt_self_signed_kid:
        raise InvalidTokenError(f"unexpected kid: {kid!r}")
    return decode_jwt(
        token,
        config.jwt_public_key,
        issuer=config.jwt_issuer,
        audience=audience,
    )


def verify_managed_agent_token(config: JwtTokenConfig, token: str) -> dict[str, Any]:
    """Verify a managed-agent (proxy) token and return its claims.

    Positive judgement is ``token_class == "managed_agent"``; ``aud`` and the
    ``client_id != registry`` exclusion are defense-in-depth. Raises
    :class:`InvalidTokenError` on any failure.
    """
    claims = _decode_self_signed(config, token, config.managed_agents_audience)

    if claims.get(TOKEN_CLASS_CLAIM) != TOKEN_CLASS_MANAGED_AGENT:
        raise InvalidTokenError(f"wrong token_class for managed-agent: {claims.get(TOKEN_CLASS_CLAIM)!r}")

    # client_id is required on managed-agent tokens (the token endpoint mandates it).
    # Require it positively rather than relying on "!= registry" treating a missing
    # value as acceptable.
    client_id = claims.get("client_id")
    if not client_id:
        raise InvalidTokenError("managed-agent token is missing the required client_id claim")

    # First-party CRUD principal must never act as a managed agent over /proxy.
    if client_id == config.registry_client_id:
        raise InvalidTokenError("registry client_id is not permitted on managed-agent (proxy) tokens")

    return claims


def verify_crud_session_token(
    config: JwtTokenConfig,
    token: str,
    *,
    expected_token_type: str | None = None,
) -> dict[str, Any]:
    """Verify a CRUD-session (cookie) token and return its claims.

    Positive judgement is ``token_class == "crud_session"``; ``aud`` and the
    ``client_id == registry`` requirement are defense-in-depth. When
    ``expected_token_type`` is given, ``token_type`` must match (access vs refresh).
    Raises :class:`InvalidTokenError` on any failure.
    """
    claims = _decode_self_signed(config, token, config.crud_services_audience)

    if claims.get(TOKEN_CLASS_CLAIM) != TOKEN_CLASS_CRUD_SESSION:
        raise InvalidTokenError(f"wrong token_class for crud-session: {claims.get(TOKEN_CLASS_CLAIM)!r}")

    if claims.get("client_id") != config.registry_client_id:
        raise InvalidTokenError("crud-session tokens must carry the registry client_id")

    if expected_token_type is not None and claims.get("token_type") != expected_token_type:
        raise InvalidTokenError(f"wrong token_type: {claims.get('token_type')!r}, expected {expected_token_type!r}")

    return claims
