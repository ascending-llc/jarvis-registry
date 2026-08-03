from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient, Request

from registry.core.a2a_proxy import A2AProxyClientRegistry
from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.jwt_utils import decode_jwt_unverified
from registry_pkgs.models.federation import AgentCoreRuntimeJwtConfig

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_KEY = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")


def _jwt_signing_config() -> JwtSigningConfig:
    return JwtSigningConfig(
        jwt_private_key=_PRIVATE_KEY,
        jwt_issuer="https://registry.test",
        jwt_self_signed_kid="a2a-proxy-test-key",
        jwt_audience="jarvis-services",
    )


def _runtime_jwt_config(
    *,
    issuer_host: str = "issuer-one.example.com",
    audience: str = "audience-one",
) -> AgentCoreRuntimeJwtConfig:
    return AgentCoreRuntimeJwtConfig(
        discoveryUrl=f"https://{issuer_host}/.well-known/openid-configuration",
        audiences=[audience],
    )


def _signed_claims(client: AsyncClient) -> dict:
    request = Request("POST", "https://agent.example.com")
    authed_request = next(client.auth.auth_flow(request))
    token = authed_request.headers["Authorization"].split(" ", 1)[1]
    return decode_jwt_unverified(token)


async def _close_registry(registry: A2AProxyClientRegistry) -> None:
    await registry.close()


async def test_get_with_unchanged_config_reuses_cached_client_and_auth():
    registry = A2AProxyClientRegistry(
        jwt_signing_config=_jwt_signing_config(),
        jwt_subject="jarvis-registry",
        jwt_expires_in_seconds=300,
    )
    runtime_jwt_config = _runtime_jwt_config()

    try:
        client = registry.get("agent-one", agentcore_jwt=True, runtime_jwt_config=runtime_jwt_config)
        auth = client.auth

        same_client = registry.get("agent-one", agentcore_jwt=True, runtime_jwt_config=runtime_jwt_config)

        assert same_client is client
        assert same_client.auth is auth
    finally:
        await _close_registry(registry)


async def test_get_with_changed_runtime_jwt_config_swaps_auth_on_same_client():
    registry = A2AProxyClientRegistry(
        jwt_signing_config=_jwt_signing_config(),
        jwt_subject="jarvis-registry",
        jwt_expires_in_seconds=300,
    )
    first_config = _runtime_jwt_config(issuer_host="issuer-one.example.com", audience="audience-one")
    second_config = _runtime_jwt_config(issuer_host="issuer-two.example.com", audience="audience-two")

    try:
        client = registry.get("agent-one", agentcore_jwt=True, runtime_jwt_config=first_config)
        first_auth = client.auth
        first_claims = _signed_claims(client)

        same_client = registry.get("agent-one", agentcore_jwt=True, runtime_jwt_config=second_config)
        second_claims = _signed_claims(same_client)

        assert same_client is client
        assert same_client.auth is not first_auth
        assert first_claims["iss"] == "https://issuer-one.example.com"
        assert first_claims["aud"] == "audience-one"
        assert second_claims["iss"] == "https://issuer-two.example.com"
        assert second_claims["aud"] == "audience-two"
    finally:
        await _close_registry(registry)


async def test_get_for_non_agentcore_agent_has_no_auth_and_reuses_client():
    registry = A2AProxyClientRegistry(
        jwt_signing_config=_jwt_signing_config(),
        jwt_subject="jarvis-registry",
        jwt_expires_in_seconds=300,
    )

    try:
        client = registry.get("agent-one", agentcore_jwt=False)
        same_client = registry.get("agent-one", agentcore_jwt=False)

        assert same_client is client
        assert client.auth is None
    finally:
        await _close_registry(registry)
