"""Unit tests for oauth_flow redirect_uri helpers."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from auth_server.routes.oauth_flow import (
    _is_client_authorized_for_grant,
    _is_registered_redirect_uri,
    _redirect_error_to_client,
    _resolve_client_metadata,
    _trusted_error_redirect_uris,
    _validate_client_credentials,
    _validate_known_client_for_redirect,
)
from registry_pkgs.core.client_categories import REGISTRY_CLI_CLIENT_ID
from registry_pkgs.core.downstream_oauth import DEVICE_CODE_GRANT_TYPE
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
            {"redirect_uris": ["http://localhost/callback"]},
        )

        assert (
            _validate_known_client_for_redirect(
                "known-client",
                "http://localhost/callback",
                store,
            )
            is None
        )


def test_trusted_error_redirect_uris_adds_exact_deployment_callback() -> None:
    callback = "https://jarvis.example.com/api/mcp/jarvis_registry/oauth/callback"
    with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
        mock_settings.jwt_issuer = "https://jarvis.example.com"
        trusted_uris = _trusted_error_redirect_uris()

    assert callback in trusted_uris
    assert is_safe_unverified_redirect_target(callback, trusted_uris) is True
    assert is_safe_unverified_redirect_target(f"{callback}/", trusted_uris) is False
    assert is_safe_unverified_redirect_target(f"{callback}/extra", trusted_uris) is False


class TestResolveClientMetadata:
    def test_cli_returns_static_metadata(self) -> None:
        store = InMemoryOAuthStateStore()
        result = _resolve_client_metadata(REGISTRY_CLI_CLIENT_ID, store)

        assert result is not None
        assert result["client_id"] == REGISTRY_CLI_CLIENT_ID
        assert result["client_name"] == "Jarvis Registry CLI"
        assert result["token_endpoint_auth_method"] == "none"

    def test_cli_does_not_hit_store(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client(REGISTRY_CLI_CLIENT_ID, {"client_id": REGISTRY_CLI_CLIENT_ID, "client_name": "From Redis"})

        result = _resolve_client_metadata(REGISTRY_CLI_CLIENT_ID, store)
        assert result["client_name"] == "Jarvis Registry CLI"

    def test_dcr_client_returns_store_metadata(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client("mcp-client-abc", {"client_id": "mcp-client-abc", "scope": "mcp-proxy-ops"})

        result = _resolve_client_metadata("mcp-client-abc", store)
        assert result is not None
        assert result["client_id"] == "mcp-client-abc"

    def test_unknown_client_returns_none(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _resolve_client_metadata("unknown-client", store) is None


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


class TestValidateClientCredentials:
    def test_registry_app_correct_secret(self) -> None:
        store = InMemoryOAuthStateStore()
        with patch("auth_server.routes.oauth_flow.settings") as s:
            s.registry_app_name = "jarvis-registry"
            s.registry_client_secret = "correct-secret"
            assert _validate_client_credentials("jarvis-registry", "correct-secret", store) is True

    def test_registry_app_wrong_secret(self) -> None:
        store = InMemoryOAuthStateStore()
        with patch("auth_server.routes.oauth_flow.settings") as s:
            s.registry_app_name = "jarvis-registry"
            s.registry_client_secret = "correct-secret"
            assert _validate_client_credentials("jarvis-registry", "wrong-secret", store) is False

    def test_cli_public_client_no_secret(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _validate_client_credentials(REGISTRY_CLI_CLIENT_ID, None, store) is True

    def test_cli_public_client_ignores_secret(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _validate_client_credentials(REGISTRY_CLI_CLIENT_ID, "any-secret", store) is True

    def test_dcr_client_delegates_to_store(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client("mcp-client-x", {"client_secret": "s", "token_endpoint_auth_method": "client_secret_post"})
        assert _validate_client_credentials("mcp-client-x", "s", store) is True
        assert _validate_client_credentials("mcp-client-x", "wrong", store) is False

    def test_unknown_client_returns_false(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _validate_client_credentials("unknown", None, store) is False


class TestIsClientAuthorizedForGrant:
    def test_registry_app_always_authorized(self) -> None:
        store = InMemoryOAuthStateStore()
        with patch("auth_server.routes.oauth_flow.settings") as s:
            s.registry_app_name = "jarvis-registry"
            assert _is_client_authorized_for_grant("jarvis-registry", "authorization_code", store) is True

    def test_cli_device_code_allowed(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _is_client_authorized_for_grant(REGISTRY_CLI_CLIENT_ID, DEVICE_CODE_GRANT_TYPE, store) is True

    def test_cli_refresh_token_allowed(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _is_client_authorized_for_grant(REGISTRY_CLI_CLIENT_ID, "refresh_token", store) is True

    def test_cli_authorization_code_denied(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _is_client_authorized_for_grant(REGISTRY_CLI_CLIENT_ID, "authorization_code", store) is False

    def test_dcr_client_with_matching_grant(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client("mcp-client-y", {"grant_types": ["authorization_code", "refresh_token"]})
        assert _is_client_authorized_for_grant("mcp-client-y", "authorization_code", store) is True

    def test_dcr_client_without_matching_grant(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client("mcp-client-y", {"grant_types": ["authorization_code"]})
        assert _is_client_authorized_for_grant("mcp-client-y", DEVICE_CODE_GRANT_TYPE, store) is False

    def test_unknown_client_returns_false(self) -> None:
        store = InMemoryOAuthStateStore()
        assert _is_client_authorized_for_grant("unknown", "authorization_code", store) is False
