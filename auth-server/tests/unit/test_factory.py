from unittest.mock import Mock

import pytest

from auth_server.core.config import AuthSettings, settings
from auth_server.providers.factory import _create_entra_provider, _create_google_provider, get_auth_provider
from auth_server.providers.google import GoogleProvider
from registry_pkgs.core.scope_groups import SCOPE_GROUP_OBJECT_ID_FIELDS, entra_scope_group_ids


def _google_config(**overrides) -> dict:
    config = {
        "client_id": "google-client",
        "client_secret": "google-secret",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
        "scopes": ["openid", "email", "profile"],
        "grant_type": "authorization_code",
        "allowed_hd": "corp.example",
    }
    config.update(overrides)
    return config


@pytest.mark.unit
@pytest.mark.auth
class TestCreateGoogleProvider:
    def test_constructs_provider_from_config(self):
        cic = Mock()
        provider = _create_google_provider(_google_config(), cic)

        assert isinstance(provider, GoogleProvider)
        assert provider.client_id == "google-client"
        assert provider.client_secret == "google-secret"
        assert provider.allowed_hd == "corp.example"
        assert provider._cloud_identity_client is cic
        assert provider.auth_url == "https://accounts.google.com/o/oauth2/v2/auth"
        assert provider.token_url == "https://oauth2.googleapis.com/token"
        assert provider.jwks_url == "https://www.googleapis.com/oauth2/v3/certs"

    def test_defaults_allowed_hd_to_empty(self):
        provider = _create_google_provider(_google_config(allowed_hd=None) | {"allowed_hd": ""}, Mock())
        assert provider.allowed_hd == ""

    @pytest.mark.parametrize("missing", ["client_id", "client_secret", "auth_url", "token_url", "jwks_url"])
    def test_missing_required_config_raises(self, missing):
        config = _google_config()
        config[missing] = ""

        with pytest.raises(ValueError, match="Missing required Google configuration"):
            _create_google_provider(config, Mock())


@pytest.mark.unit
@pytest.mark.auth
class TestGetAuthProviderDispatch:
    def test_google_branch_routes_to_google_provider(self):
        oauth2_config = {"providers": {"google": _google_config()}}
        provider = get_auth_provider("google", oauth2_config, Mock(), settings)
        assert isinstance(provider, GoogleProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown auth provider"):
            get_auth_provider("nope", {"providers": {}}, Mock(), settings)


@pytest.mark.unit
def test_entra_factory_injects_scope_group_bindings() -> None:
    config = {
        "tenant_id": "tenant",
        "client_id": "client",
        "client_secret": "secret",
        "auth_url": "https://example.com/auth",
        "token_url": "https://example.com/token",
        "jwks_url": "https://example.com/jwks",
        "logout_url": "https://example.com/logout",
        "user_info_url": "https://graph.microsoft.com/v1.0/me",
    }
    provider = get_auth_provider("entra", {"providers": {"entra": config}}, Mock(), settings)
    assert provider.scope_group_ids == entra_scope_group_ids(settings)


@pytest.mark.unit
def test_entra_factory_rejects_empty_bindings_even_when_startup_checks_are_disabled() -> None:
    unconfigured = AuthSettings(
        x_jarvis_registry_import_checks="disabled", **dict.fromkeys(SCOPE_GROUP_OBJECT_ID_FIELDS.values())
    )
    with pytest.raises(ValueError, match="Entra scope groups are not configured"):
        _create_entra_provider({}, unconfigured)
