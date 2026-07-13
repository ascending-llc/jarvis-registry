from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from registry_pkgs.core.agentcore_jwt import mint_agentcore_runtime_jwt
from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.jwt_utils import decode_jwt_unverified, get_token_unverified_header
from registry_pkgs.models.federation import AgentCoreRuntimeJwtConfig

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_KEY = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")

_KID = "agentcore-test-key"


def _signing_config() -> JwtSigningConfig:
    return JwtSigningConfig(
        jwt_private_key=_PRIVATE_KEY,
        jwt_issuer="https://registry.test",
        jwt_self_signed_kid=_KID,
        jwt_audience="jarvis-services",
    )


def test_mints_jwt_with_discovery_origin_and_first_audience():
    token = mint_agentcore_runtime_jwt(
        AgentCoreRuntimeJwtConfig(
            discoveryUrl="https://issuer.example.com/.well-known/openid-configuration",
            audiences=["agentcore-runtime", "secondary-audience"],
        ),
        subject="jarvis-registry",
        signing=_signing_config(),
        expires_in_seconds=300,
    )

    claims = decode_jwt_unverified(token)
    assert claims["iss"] == "https://issuer.example.com"
    assert claims["aud"] == "agentcore-runtime"
    assert claims["sub"] == "jarvis-registry"
    assert get_token_unverified_header(token)["kid"] == _KID


def test_falls_back_to_signing_issuer_and_audience_without_runtime_jwt_config():
    token = mint_agentcore_runtime_jwt(
        None,
        subject="jarvis-registry",
        signing=_signing_config(),
        expires_in_seconds=300,
    )

    claims = decode_jwt_unverified(token)
    assert claims["iss"] == "https://registry.test"
    assert claims["aud"] == "jarvis-services"


def test_falls_back_to_signing_audience_when_runtime_audiences_are_empty():
    token = mint_agentcore_runtime_jwt(
        AgentCoreRuntimeJwtConfig(discoveryUrl="https://issuer.example.com/.well-known/openid-configuration"),
        subject="jarvis-registry",
        signing=_signing_config(),
        expires_in_seconds=300,
    )

    claims = decode_jwt_unverified(token)
    assert claims["iss"] == "https://issuer.example.com"
    assert claims["aud"] == "jarvis-services"


def test_adds_normalized_client_scope_and_custom_claims():
    token = mint_agentcore_runtime_jwt(
        AgentCoreRuntimeJwtConfig(
            allowedClients=[" jarvis-registry "],
            allowedScopes=[" sync:read ", "", " tools:read "],
            customClaims={
                "tenant": " prod ",
                "nested": {"value": " trimmed "},
                "list": [" a ", " b "],
            },
        ),
        subject="jarvis-registry",
        signing=_signing_config(),
        expires_in_seconds=300,
    )

    claims = decode_jwt_unverified(token)
    assert claims["client_id"] == "jarvis-registry"
    assert claims["scope"] == "sync:read tools:read"
    assert claims["tenant"] == "prod"
    assert claims["nested"] == {"value": "trimmed"}
    assert claims["list"] == ["a", "b"]
