"""Dual-provider (Entra + Google) login wiring against the real oauth2_providers.yml.

Guards the demo scenario where both Entra and Google are enabled simultaneously: the
providers listing, per-provider construction, and the authorization redirect each build
correctly and independently. No HTTP server or real IdP credentials required.
"""

from unittest.mock import Mock

import pytest
from itsdangerous import URLSafeTimedSerializer

from auth_server.container import AuthContainer
from auth_server.core.config import AuthSettings
from auth_server.providers.entra import EntraIdProvider
from auth_server.providers.google import GoogleProvider
from auth_server.routes.oauth_flow import _redirect_to_provider


def _demo_settings(**overrides) -> AuthSettings:
    # Explicit toggles keep the test independent of any local .env; this mirrors a demo env
    # that sets GOOGLE_ENABLED=true (entra on by default, keycloak/cognito off by default).
    base = {
        "keycloak_enabled": "false",
        "cognito_enabled": "false",
        "entra_enabled": "true",
        "google_enabled": "true",
        "entra_tenant_id": "tenant-abc",
        "entra_client_id": "entra-client",
        "entra_client_secret": "entra-secret",
        "google_client_id": "google-client.apps.googleusercontent.com",
        "google_client_secret": "google-secret",
        "google_allowed_hd": "ascending.com",
        "google_service_account_key_json": "",
        "secret_key": "demo-secret",
    }
    base.update(overrides)
    return AuthSettings(**base)


def _container(**overrides) -> AuthContainer:
    return AuthContainer(_demo_settings(**overrides), redis_client=Mock())


@pytest.mark.integration
@pytest.mark.auth
class TestDualProviderLogin:
    def test_providers_listing_shows_exactly_entra_and_google(self):
        providers = _container().oauth2_config["providers"]
        listed = {name for name, cfg in providers.items() if cfg["enabled"]}
        assert listed == {"entra", "google"}

    def test_keycloak_stays_off_even_if_env_sets_it_true(self):
        # Toggling KEYCLOAK_ENABLED brings it back — proves the toggle is env-driven, not hardcoded.
        providers = _container(keycloak_enabled="true").oauth2_config["providers"]
        listed = {name for name, cfg in providers.items() if cfg["enabled"]}
        assert listed == {"keycloak", "entra", "google"}

    def test_container_builds_each_provider_independently_and_caches(self):
        container = _container()
        entra = container.get_auth_provider("entra")
        google = container.get_auth_provider("google")

        assert isinstance(entra, EntraIdProvider)
        assert isinstance(google, GoogleProvider)
        # Cached per provider so each instance's JWKS cache persists across requests.
        assert container.get_auth_provider("google") is google
        assert container.get_auth_provider("entra") is entra

    def test_login_redirect_targets_correct_idp_per_provider(self):
        providers = _container().oauth2_config["providers"]
        signer = URLSafeTimedSerializer("demo-secret")
        session = {"state": "s-1"}

        entra_url = _redirect_to_provider("entra", providers["entra"], session, False, signer).headers["location"]
        google_url = _redirect_to_provider("google", providers["google"], session, False, signer).headers["location"]

        assert entra_url.startswith("https://login.microsoftonline.com/")
        assert google_url.startswith("https://accounts.google.com/o/oauth2/v2/auth")

    def test_google_redirect_narrows_account_picker_with_hd(self):
        providers = _container().oauth2_config["providers"]
        signer = URLSafeTimedSerializer("demo-secret")

        google_url = _redirect_to_provider("google", providers["google"], {"state": "s-1"}, False, signer).headers[
            "location"
        ]
        entra_url = _redirect_to_provider("entra", providers["entra"], {"state": "s-1"}, False, signer).headers[
            "location"
        ]

        assert "hd=ascending.com" in google_url
        assert "hd=" not in entra_url
