from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from registry_pkgs.core import agentcore_jwt
from registry_pkgs.core.agentcore_jwt import mint_agentcore_runtime_jwt, sign_agentcore_jwt
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
        registry_app_name="jarvis-registry",
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


@pytest.fixture
def mint(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(agentcore_jwt, "_token_cache", {})
    mint = Mock(side_effect=["first-token", "second-token", "third-token"])
    monkeypatch.setattr(agentcore_jwt, "mint_agentcore_runtime_jwt", mint)
    return mint


@pytest.mark.parametrize(
    ("server_id", "subject", "audience", "kid"),
    [
        ("server-2", "jarvis", "runtime-a", _KID),
        ("server-1", "another-subject", "runtime-a", _KID),
        ("server-1", "jarvis", "runtime-b", _KID),
        ("server-1", "jarvis", "runtime-a", "rotated-key"),
    ],
)
def test_sign_reuses_identical_inputs_and_separates_changed_inputs(
    mint: Mock, server_id: str, subject: str, audience: str, kid: str
) -> None:
    signing = _signing_config()
    runtime = AgentCoreRuntimeJwtConfig(audiences=["runtime-a"])
    first = sign_agentcore_jwt(runtime, server_id="server-1", subject="jarvis", signing=signing)
    repeated = sign_agentcore_jwt(runtime.model_copy(), server_id="server-1", subject="jarvis", signing=signing)
    assert first == repeated == "first-token"
    mint.assert_called_once_with(runtime, subject="jarvis", signing=signing, expires_in_seconds=3600)

    updated = sign_agentcore_jwt(
        AgentCoreRuntimeJwtConfig(audiences=[audience]),
        server_id=server_id,
        subject=subject,
        signing=signing.model_copy(update={"jwt_self_signed_kid": kid}),
    )
    assert updated == "second-token"
    assert mint.call_count == 2


def test_sign_refreshes_at_expiry_and_prunes_other_expired_tokens(mint: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Mock(return_value=1000.0)
    monkeypatch.setattr(agentcore_jwt, "monotonic", clock)
    signing = _signing_config()
    first = sign_agentcore_jwt(None, server_id="server-1", subject="jarvis", signing=signing)
    sign_agentcore_jwt(None, server_id="server-2", subject="jarvis", signing=signing)

    clock.return_value += 3539
    cached = sign_agentcore_jwt(None, server_id="server-1", subject="jarvis", signing=signing)
    assert cached == first
    clock.return_value += 1
    renewed = sign_agentcore_jwt(None, server_id="server-1", subject="jarvis", signing=signing)
    assert renewed == "third-token"
    assert mint.call_count == 3
    assert len(agentcore_jwt._token_cache) == 1


def test_failed_mint_can_be_retried(mint: Mock) -> None:
    mint.side_effect = [ValueError("invalid signing key"), "new-token"]
    signing = _signing_config()
    with pytest.raises(ValueError, match="invalid signing key"):
        sign_agentcore_jwt(None, server_id="server", subject="jarvis", signing=signing)

    token = sign_agentcore_jwt(None, server_id="server", subject="jarvis", signing=signing)
    assert token == "new-token"
    assert mint.call_count == 2
