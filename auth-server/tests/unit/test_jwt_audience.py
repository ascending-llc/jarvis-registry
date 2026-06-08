import pytest

from auth_server.core.config import settings
from auth_server.services.cognito_validator_service import SimplifiedCognitoValidator
from registry_pkgs.core.jwt_tokens import mint_crud_session_token, mint_managed_agent_token


@pytest.fixture
def validator() -> SimplifiedCognitoValidator:
    return SimplifiedCognitoValidator()


@pytest.fixture
def cfg():
    return settings.jwt_token_config


@pytest.mark.unit
@pytest.mark.auth
class TestSelfSignedTokenValidation:
    def test_accepts_managed_agent_token(self, validator, cfg):
        token = mint_managed_agent_token(
            cfg,
            subject="alice",
            client_id="mcp-client-abc",
            expires_in_seconds=3600,
            extra_claims={"token_use": "access", "scope": "a b"},
        )

        result = validator.validate_self_signed_token(token)

        assert result["valid"] is True
        assert result["method"] == "self_signed"
        assert result["client_id"] == "mcp-client-abc"
        assert result["username"] == "alice"
        assert result["scopes"] == ["a", "b"]

    def test_rejects_crud_session_token(self, validator, cfg):
        # A dashboard cookie token must never validate as a managed-agent token.
        token = mint_crud_session_token(
            cfg,
            subject="bob",
            token_type="access_token",
            expires_in_seconds=3600,
            extra_claims={"token_use": "access"},
        )

        with pytest.raises(ValueError):
            validator.validate_self_signed_token(token)

    def test_rejects_registry_self_token(self, validator, cfg):
        # Registry's own-login token (client_id == registry) is inert on the proxy path.
        token = mint_managed_agent_token(
            cfg,
            subject="self",
            client_id=cfg.registry_client_id,
            expires_in_seconds=3600,
            extra_claims={"token_use": "access"},
        )

        with pytest.raises(ValueError):
            validator.validate_self_signed_token(token)

    def test_rejects_non_access_token_use(self, validator, cfg):
        token = mint_managed_agent_token(
            cfg,
            subject="alice",
            client_id="mcp-client-abc",
            expires_in_seconds=3600,
            extra_claims={"token_use": "id"},
        )

        with pytest.raises(ValueError):
            validator.validate_self_signed_token(token)
