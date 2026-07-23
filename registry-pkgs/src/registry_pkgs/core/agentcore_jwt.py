"""JWT minting helpers for AWS Bedrock AgentCore Runtime access."""

import logging
from typing import Any
from urllib.parse import urlparse

from redis import Redis

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.models.federation import AgentCoreRuntimeAccessConfig, AgentCoreRuntimeJwtConfig

logger = logging.getLogger(__name__)


def _normalize_claim_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalize_claim_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_claim_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _normalize_claim_value(item) for key, item in value.items()}
    return value


def _issuer_from_discovery_url(discovery_url: str) -> str:
    parsed = urlparse(discovery_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("runtimeAccess.jwt.discoveryUrl must include a scheme and host")
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_agentcore_extra_claims(runtime_jwt_config: AgentCoreRuntimeJwtConfig) -> dict[str, Any]:
    extra_claims: dict[str, Any] = {}
    if runtime_jwt_config.allowedClients:
        extra_claims["client_id"] = _normalize_claim_value(runtime_jwt_config.allowedClients[0])
    if runtime_jwt_config.allowedScopes:
        cleaned_scopes = [
            scope for scope in (_normalize_claim_value(scope) for scope in runtime_jwt_config.allowedScopes) if scope
        ]
        if cleaned_scopes:
            extra_claims["scope"] = " ".join(cleaned_scopes)
    if runtime_jwt_config.customClaims:
        extra_claims.update(_normalize_claim_value(runtime_jwt_config.customClaims))
    return extra_claims


def mint_agentcore_runtime_jwt(
    runtime_jwt_config: AgentCoreRuntimeJwtConfig | None,
    *,
    subject: str,
    signing: JwtSigningConfig,
    expires_in_seconds: int,
) -> str:
    """Mint a self-signed JWT accepted by a specific AgentCore runtime."""
    issuer = signing.jwt_issuer
    audience = signing.jwt_audience
    extra_claims: dict[str, Any] = {}

    if runtime_jwt_config is not None:
        if runtime_jwt_config.discoveryUrl:
            issuer = _issuer_from_discovery_url(runtime_jwt_config.discoveryUrl)
        if runtime_jwt_config.audiences:
            audience = _normalize_claim_value(runtime_jwt_config.audiences[0])
        extra_claims = _build_agentcore_extra_claims(runtime_jwt_config)

    payload = build_jwt_payload(
        subject=subject,
        issuer=issuer,
        audience=audience,
        expires_in_seconds=expires_in_seconds,
        extra_claims=extra_claims or None,
    )
    return encode_jwt(payload, signing.jwt_private_key, kid=signing.jwt_self_signed_kid)


_AGENTCORE_JWT_TTL_SECONDS = 3600
_AGENTCORE_JWT_LEEWAY_SECONDS = 60


def parse_agentcore_runtime_access(
    runtime_access: AgentCoreRuntimeAccessConfig | dict[str, Any],
) -> AgentCoreRuntimeAccessConfig:
    """Return a validated runtime-access model from stored MCP/A2A config."""
    if isinstance(runtime_access, AgentCoreRuntimeAccessConfig):
        return runtime_access
    return AgentCoreRuntimeAccessConfig.model_validate(runtime_access)


def sign_agentcore_jwt(
    runtime_jwt_config: AgentCoreRuntimeJwtConfig | None,
    *,
    signing: JwtSigningConfig,
    cache_key: str,
    redis_client: Redis | None = None,
) -> str:
    """Mint (or return a cached) AgentCore Runtime JWT.

    This function is synchronous so it can be used inside agno's
    ``MCPTools.header_provider`` callback, which is never awaited.
    """
    if redis_client is not None:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return cached.decode("utf-8") if isinstance(cached, bytes) else cached
        except Exception:
            logger.exception("Failed to read cached AgentCore JWT for %s", cache_key)

    token = mint_agentcore_runtime_jwt(
        runtime_jwt_config,
        subject=signing.registry_app_name,
        signing=signing,
        expires_in_seconds=_AGENTCORE_JWT_TTL_SECONDS,
    )

    if redis_client is not None:
        try:
            redis_client.setex(
                cache_key,
                _AGENTCORE_JWT_TTL_SECONDS - _AGENTCORE_JWT_LEEWAY_SECONDS,
                token,
            )
        except Exception:
            logger.exception("Failed to cache AgentCore JWT for %s", cache_key)

    return token
