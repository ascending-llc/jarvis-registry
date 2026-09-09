"""Unit tests for oauth_flow redirect_uri helpers."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from itsdangerous import URLSafeTimedSerializer

from auth_server.routes.oauth_flow import (
    _is_registered_redirect_uri,
    _redirect_error_to_client,
    _redirect_to_provider,
    _trusted_error_redirect_uris,
    _validate_known_client_for_redirect,
)
from registry_pkgs.core.client_categories import AUTHORIZATION_CODE_GRANT_TYPE
from registry_pkgs.core.redirect_uri import is_safe_unverified_redirect_target
from tests.support.oauth_state_store import InMemoryOAuthStateStore


class TestIsRegisteredRedirectUri:
    def test_no_registered_uris_rejected(self) -> None:
        assert _is_registered_redirect_uri({}, "https://anything.example.com/cb") is False
        assert _is_registered_redirect_uri({"redirect_uris": []}, "https://anything.example.com/cb") is False

    def test_exact_match_non_loopback(self) -> None:
        meta = {"redirect_uris": ["https://app.example.com/cb"]}
        assert _is_registered_redirect_uri(meta, "https://app.example.com/cb") is True

    def test_non_loopback_port_mismatch_rejected(self) -> None:
        meta = {"redirect_uris": ["https://app.example.com:8000/cb"]}
        assert _is_registered_redirect_uri(meta, "https://app.example.com:9000/cb") is False

    def test_loopback_port_exemption(self) -> None:
        meta = {"redirect_uris": ["http://127.0.0.1:1234/cb"]}
        assert _is_registered_redirect_uri(meta, "http://127.0.0.1:55555/cb") is True

    def test_matches_any_registered_uri(self) -> None:
        meta = {"redirect_uris": ["https://a.example.com/cb", "https://b.example.com/cb"]}
        assert _is_registered_redirect_uri(meta, "https://b.example.com/cb") is True


class TestValidateKnownClientForRedirect:
    def test_unknown_client_returns_typed_invalid_client_error(self) -> None:
        error = _validate_known_client_for_redirect(
            "unknown-client",
            "http://localhost/callback",
            InMemoryOAuthStateStore(),
        )

        assert error is not None
        assert error.error == "invalid_client"
        assert error.error_description == "Unknown client_id"

    def test_unregistered_redirect_returns_typed_invalid_request_error(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client(
            "known-client",
            {"redirect_uris": ["https://client.example/callback"]},
        )

        error = _validate_known_client_for_redirect(
            "known-client",
            "http://localhost/callback",
            store,
        )

        assert error is not None
        assert error.error == "invalid_request"
        assert error.error_description == "redirect_uri is not registered for this client"

    def test_registered_redirect_returns_none(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client(
            "known-client",
            {
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": [AUTHORIZATION_CODE_GRANT_TYPE],
            },
        )

        assert (
            _validate_known_client_for_redirect(
                "known-client",
                "http://localhost/callback",
                store,
            )
            is None
        )

    def test_registered_redirect_without_authorization_code_returns_unauthorized_client(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client(
            "known-client",
            {
                "redirect_uris": ["http://localhost/callback"],
                "grant_types": ["refresh_token"],
            },
        )

        error = _validate_known_client_for_redirect(
            "known-client",
            "http://localhost/callback",
            store,
        )

        assert error is not None
        assert error.error == "unauthorized_client"
        assert error.error_description == "client is not authorized for authorization_code"


def test_trusted_error_redirect_uris_adds_exact_deployment_callback() -> None:
    callback = "https://jarvis.example.com/api/mcp/jarvis_registry/oauth/callback"
    with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
        mock_settings.jwt_issuer = "https://jarvis.example.com"
        trusted_uris = _trusted_error_redirect_uris()

    assert callback in trusted_uris
    assert is_safe_unverified_redirect_target(callback, trusted_uris) is True
    assert is_safe_unverified_redirect_target(f"{callback}/", trusted_uris) is False
    assert is_safe_unverified_redirect_target(f"{callback}/extra", trusted_uris) is False


def _redirect_to_provider_query(provider: str, provider_config: dict) -> dict:
    signer = URLSafeTimedSerializer("test-secret")
    session_data = {"state": "state-1"}
    with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
        mock_settings.auth_server_external_url = "http://localhost:8888"
        mock_settings.auth_server_api_prefix = ""
        mock_settings.oauth2_temp_session_cookie_name = "oauth2_temp_session"
        mock_settings.oauth_session_ttl_seconds = 600
        mock_settings.session_cookie_secure = False
        response = _redirect_to_provider(provider, provider_config, session_data, is_https=False, signer=signer)
    return parse_qs(urlparse(response.headers["location"]).query)


def _google_config(**overrides) -> dict:
    config = {
        "client_id": "google-client",
        "response_type": "code",
        "scopes": ["openid", "email", "profile"],
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "allowed_hd": "corp.example",
    }
    config.update(overrides)
    return config


def test_redirect_to_provider_adds_hd_for_google_when_configured() -> None:
    query = _redirect_to_provider_query("google", _google_config())
    assert query["hd"] == ["corp.example"]


def test_redirect_to_provider_omits_hd_for_google_when_unset() -> None:
    query = _redirect_to_provider_query("google", _google_config(allowed_hd=""))
    assert "hd" not in query


def test_redirect_to_provider_never_adds_hd_for_non_google() -> None:
    # allowed_hd on a non-google config must not leak an hd param.
    query = _redirect_to_provider_query("entra", _google_config())
    assert "hd" not in query


def test_redirect_error_to_client_builds_302_with_oauth_error() -> None:
    response = _redirect_error_to_client(
        "http://localhost/callback?existing=1",
        "invalid_client",
        "Unknown client_id",
        "client-state",
    )

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query == {
        "existing": ["1"],
        "error": ["invalid_client"],
        "error_description": ["Unknown client_id"],
        "state": ["client-state"],
    }
