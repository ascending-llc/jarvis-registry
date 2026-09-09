import pytest

from auth_server.core.config import AuthSettings
from auth_server.utils.config_loader import OAuth2ConfigLoader


@pytest.mark.unit
@pytest.mark.auth
class TestEnabledCoercion:
    def test_all_providers_enabled_is_real_bool(self):
        # Substitution yields strings for the toggles; every provider's `enabled` must still
        # reach downstream consumers as a real bool.
        providers = OAuth2ConfigLoader(AuthSettings(google_enabled="true", entra_enabled="false")).get_config()[
            "providers"
        ]

        for name in ("keycloak", "cognito", "entra", "google"):
            assert isinstance(providers[name]["enabled"], bool), name

    def test_every_provider_toggle_is_env_driven(self):
        providers = OAuth2ConfigLoader(
            AuthSettings(
                keycloak_enabled="true",
                cognito_enabled="true",
                entra_enabled="true",
                google_enabled="true",
            )
        ).get_config()["providers"]

        listed = {name for name, cfg in providers.items() if cfg["enabled"]}
        assert listed == {"keycloak", "cognito", "entra", "google"}

    def test_demo_case_only_google_and_entra(self):
        # Demo env: GOOGLE_ENABLED=true; entra on by default; keycloak/cognito off by default.
        providers = OAuth2ConfigLoader(AuthSettings(google_enabled="true")).get_config()["providers"]

        listed = {name for name, cfg in providers.items() if cfg["enabled"]}
        assert listed == {"entra", "google"}

    def test_blank_toggle_falls_back_to_default(self):
        # A blank env value (e.g. `ENTRA_ENABLED=`) is a valid string and must not crash startup;
        # `${VAR:-default}` then applies the default.
        providers = OAuth2ConfigLoader(
            AuthSettings(keycloak_enabled="", cognito_enabled="", entra_enabled="", google_enabled="")
        ).get_config()["providers"]

        assert providers["entra"]["enabled"] is True  # only entra defaults on
        assert providers["google"]["enabled"] is False
        assert providers["keycloak"]["enabled"] is False
        assert providers["cognito"]["enabled"] is False
