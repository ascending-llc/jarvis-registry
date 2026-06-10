import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from registry_pkgs.core.config import JwtTokenConfig
from registry_pkgs.core.jwt_tokens import (
    TOKEN_CLASS_CLAIM,
    TOKEN_CLASS_CRUD_SESSION,
    TOKEN_CLASS_MANAGED_AGENT,
    mint_crud_session_token,
    mint_managed_agent_token,
    verify_crud_session_token,
    verify_managed_agent_token,
)
from registry_pkgs.core.jwt_utils import InvalidTokenError, encode_jwt

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
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

_REGISTRY_CLIENT_ID = "jarvis-registry-client"
_KID = "self-signed-key-v1"


@pytest.fixture
def cfg() -> JwtTokenConfig:
    return JwtTokenConfig(
        jwt_private_key=_PRIVATE_KEY,
        jwt_public_key=_PUBLIC_KEY,
        jwt_issuer="https://jarvis.test",
        jwt_self_signed_kid=_KID,
        managed_agents_audience="jarvis-managed-agents",
        crud_services_audience="jarvis-crud-services",
        registry_client_id=_REGISTRY_CLIENT_ID,
    )


def test_managed_agent_roundtrip(cfg):
    token = mint_managed_agent_token(
        cfg, subject="alice", client_id="mcp-client-abc", expires_in_seconds=3600, extra_claims={"scope": "x"}
    )
    claims = verify_managed_agent_token(cfg, token)
    assert claims["sub"] == "alice"
    assert claims["aud"] == "jarvis-managed-agents"
    assert claims["client_id"] == "mcp-client-abc"
    assert claims[TOKEN_CLASS_CLAIM] == TOKEN_CLASS_MANAGED_AGENT
    assert claims["scope"] == "x"


def test_crud_session_roundtrip(cfg):
    token = mint_crud_session_token(cfg, subject="bob", token_type="access_token", expires_in_seconds=3600)
    claims = verify_crud_session_token(cfg, token, expected_token_type="access_token")
    assert claims["sub"] == "bob"
    assert claims["aud"] == "jarvis-crud-services"
    assert claims["client_id"] == _REGISTRY_CLIENT_ID
    assert claims[TOKEN_CLASS_CLAIM] == TOKEN_CLASS_CRUD_SESSION
    assert claims["token_type"] == "access_token"


def test_crud_session_rejects_reserved_client_id_claim(cfg):
    with pytest.raises(ValueError, match="client_id"):
        mint_crud_session_token(
            cfg,
            subject="bob",
            token_type="access_token",
            expires_in_seconds=3600,
            extra_claims={"client_id": "evil"},
        )


def test_managed_agent_rejects_reserved_standard_claims(cfg):
    with pytest.raises(ValueError, match="aud"):
        mint_managed_agent_token(
            cfg,
            subject="alice",
            client_id="mcp-client-abc",
            expires_in_seconds=3600,
            extra_claims={"aud": "wrong-audience"},
        )


# --------------------------------------------------------------------------- #
# Cross-class rejection matrix (the security invariant)
# --------------------------------------------------------------------------- #


def test_managed_agent_token_rejected_by_crud_verifier(cfg):
    token = mint_managed_agent_token(cfg, subject="a", client_id="mcp-client-abc", expires_in_seconds=3600)
    with pytest.raises(InvalidTokenError):
        verify_crud_session_token(cfg, token)


def test_crud_token_rejected_by_managed_agent_verifier(cfg):
    token = mint_crud_session_token(cfg, subject="b", token_type="access_token", expires_in_seconds=3600)
    with pytest.raises(InvalidTokenError):
        verify_managed_agent_token(cfg, token)


def test_registry_client_id_managed_agent_token_is_inert(cfg):
    # Registry's own login mints a managed_agent token with client_id == registry.
    # It must be rejected by the proxy verifier (and also by CRUD verifier via aud).
    token = mint_managed_agent_token(cfg, subject="self", client_id=_REGISTRY_CLIENT_ID, expires_in_seconds=3600)
    with pytest.raises(InvalidTokenError):
        verify_managed_agent_token(cfg, token)
    with pytest.raises(InvalidTokenError):
        verify_crud_session_token(cfg, token)


def test_wrong_token_type_rejected(cfg):
    token = mint_crud_session_token(cfg, subject="b", token_type="refresh_token", expires_in_seconds=3600)
    with pytest.raises(InvalidTokenError):
        verify_crud_session_token(cfg, token, expected_token_type="access_token")


def test_foreign_kid_rejected(cfg):
    # A token signed with the right key but a foreign kid must be rejected early.
    from registry_pkgs.core.jwt_utils import build_jwt_payload

    payload = build_jwt_payload(
        subject="a",
        issuer=cfg.jwt_issuer,
        audience=cfg.managed_agents_audience,
        expires_in_seconds=3600,
        extra_claims={"client_id": "mcp-client-abc", TOKEN_CLASS_CLAIM: TOKEN_CLASS_MANAGED_AGENT},
    )
    token = encode_jwt(payload, _PRIVATE_KEY, kid="some-other-kid")
    with pytest.raises(InvalidTokenError):
        verify_managed_agent_token(cfg, token)


def test_missing_token_class_rejected(cfg):
    from registry_pkgs.core.jwt_utils import build_jwt_payload

    payload = build_jwt_payload(
        subject="a",
        issuer=cfg.jwt_issuer,
        audience=cfg.managed_agents_audience,
        expires_in_seconds=3600,
        extra_claims={"client_id": "mcp-client-abc"},  # no token_class
    )
    token = encode_jwt(payload, _PRIVATE_KEY, kid=_KID)
    with pytest.raises(InvalidTokenError):
        verify_managed_agent_token(cfg, token)


def test_missing_client_id_rejected(cfg):
    from registry_pkgs.core.jwt_utils import build_jwt_payload

    payload = build_jwt_payload(
        subject="a",
        issuer=cfg.jwt_issuer,
        audience=cfg.managed_agents_audience,
        expires_in_seconds=3600,
        extra_claims={TOKEN_CLASS_CLAIM: TOKEN_CLASS_MANAGED_AGENT},  # no client_id
    )
    token = encode_jwt(payload, _PRIVATE_KEY, kid=_KID)
    with pytest.raises(InvalidTokenError):
        verify_managed_agent_token(cfg, token)


def test_crud_session_missing_token_class_rejected(cfg):
    from registry_pkgs.core.jwt_utils import build_jwt_payload

    payload = build_jwt_payload(
        subject="b",
        issuer=cfg.jwt_issuer,
        audience=cfg.crud_services_audience,
        expires_in_seconds=3600,
        extra_claims={"client_id": cfg.registry_client_id},  # no token_class
    )
    token = encode_jwt(payload, _PRIVATE_KEY, kid=_KID)
    with pytest.raises(InvalidTokenError):
        verify_crud_session_token(cfg, token)


def test_crud_session_wrong_client_id_rejected(cfg):
    from registry_pkgs.core.jwt_utils import build_jwt_payload

    payload = build_jwt_payload(
        subject="b",
        issuer=cfg.jwt_issuer,
        audience=cfg.crud_services_audience,
        expires_in_seconds=3600,
        extra_claims={TOKEN_CLASS_CLAIM: TOKEN_CLASS_CRUD_SESSION, "client_id": "some-other-client"},
    )
    token = encode_jwt(payload, _PRIVATE_KEY, kid=_KID)
    with pytest.raises(InvalidTokenError):
        verify_crud_session_token(cfg, token)
