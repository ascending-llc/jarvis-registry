"""Unit tests for OAuth provider discriminator parsing and token URL generation."""

import pytest
from pydantic import TypeAdapter, ValidationError

from registry_pkgs.models.enums import OAuthProviderType
from registry_pkgs.models.oauth_provider_config import (
    Auth0Config,
    CognitoConfig,
    CustomOAuth2Config,
    EntraIDConfig,
    OAuthProviderConfig,
    OktaConfig,
)


def _adapter() -> TypeAdapter:
    return TypeAdapter(OAuthProviderConfig)


@pytest.mark.unit
class TestOAuthProviderConfigDiscriminator:
    def test_parse_cognito_config(self):
        data = {
            "providerType": OAuthProviderType.COGNITO,
            "clientId": "client-1",
            "userPoolId": "us-east-1_ABC123",
            "region": "us-east-1",
        }
        cfg = _adapter().validate_python(data)
        assert isinstance(cfg, CognitoConfig)
        assert cfg.token_url == "https://us-east-1ABC123.auth.us-east-1.amazoncognito.com/oauth2/token"

    def test_parse_cognito_config_with_domain_override(self):
        data = {
            "providerType": OAuthProviderType.COGNITO,
            "clientId": "client-1",
            "userPoolId": "us-east-1_ABC123",
            "region": "us-east-1",
            "domain": "my-custom-domain",
        }
        cfg = _adapter().validate_python(data)
        assert isinstance(cfg, CognitoConfig)
        assert cfg.token_url == "https://my-custom-domain.auth.us-east-1.amazoncognito.com/oauth2/token"

    def test_parse_auth0_config(self):
        data = {
            "providerType": OAuthProviderType.AUTH0,
            "clientId": "client-2",
            "domain": "tenant.us.auth0.com",
        }
        cfg = _adapter().validate_python(data)
        assert isinstance(cfg, Auth0Config)
        assert cfg.token_url == "https://tenant.us.auth0.com/oauth/token"

    def test_parse_okta_config_with_default_authorization_server(self):
        data = {
            "providerType": OAuthProviderType.OKTA,
            "clientId": "client-3",
            "domain": "dev-123456.okta.com",
        }
        cfg = _adapter().validate_python(data)
        assert isinstance(cfg, OktaConfig)
        assert cfg.token_url == "https://dev-123456.okta.com/oauth2/default/v1/token"

    def test_parse_okta_config_with_custom_authorization_server(self):
        data = {
            "providerType": OAuthProviderType.OKTA,
            "clientId": "client-3",
            "domain": "dev-123456.okta.com",
            "authorizationServerId": "ausabc123",
        }
        cfg = _adapter().validate_python(data)
        assert isinstance(cfg, OktaConfig)
        assert cfg.token_url == "https://dev-123456.okta.com/oauth2/ausabc123/v1/token"

    def test_parse_entra_id_config(self):
        data = {
            "providerType": OAuthProviderType.ENTRA_ID,
            "clientId": "client-4",
            "tenantId": "tenant-xyz",
        }
        cfg = _adapter().validate_python(data)
        assert isinstance(cfg, EntraIDConfig)
        assert cfg.token_url == "https://login.microsoftonline.com/tenant-xyz/oauth2/v2.0/token"

    def test_parse_custom_oauth2_config(self):
        data = {
            "providerType": OAuthProviderType.CUSTOM_OAUTH2,
            "clientId": "client-5",
            "tokenUrl": "https://oauth.example.com/token",
            "extraParams": {"audience": "my-api"},
        }
        cfg = _adapter().validate_python(data)
        assert isinstance(cfg, CustomOAuth2Config)
        assert cfg.token_url == "https://oauth.example.com/token"

    def test_invalid_provider_type_raises_validation_error(self):
        with pytest.raises(ValidationError):
            _adapter().validate_python(
                {
                    "providerType": "not_supported",
                    "clientId": "client-x",
                }
            )

    def test_missing_required_provider_field_raises_validation_error(self):
        with pytest.raises(ValidationError):
            _adapter().validate_python(
                {
                    "providerType": OAuthProviderType.COGNITO,
                    "clientId": "client-1",
                    # missing: userPoolId, region
                }
            )
