from unittest.mock import Mock

import pytest

from auth_server.providers.factory import _create_google_provider, get_auth_provider
from auth_server.providers.google import GoogleProvider


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
        provider = get_auth_provider("google", Mock(), oauth2_config, Mock())
        assert isinstance(provider, GoogleProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown auth provider"):
            get_auth_provider("nope", Mock(), {"providers": {}}, Mock())
