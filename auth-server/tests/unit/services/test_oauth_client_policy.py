"""Unit tests for OAuth client metadata and authorization policy."""

from unittest.mock import patch

from tests.support.oauth_state_store import InMemoryOAuthStateStore

from auth_server.core.config import settings
from auth_server.services.oauth_client_policy import (
    resolve_authorized_client_metadata,
    resolve_client_metadata,
)
from registry_pkgs.core.client_categories import REGISTRY_CLI_CLIENT_ID
from registry_pkgs.core.downstream_oauth import DEVICE_CODE_GRANT_TYPE


class TestResolveClientMetadata:
    def test_cli_static_metadata_takes_priority_over_store(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client(REGISTRY_CLI_CLIENT_ID, {"client_name": "From Redis"})

        result = resolve_client_metadata(REGISTRY_CLI_CLIENT_ID, store)

        assert result is not None
        assert result["client_name"] == "Jarvis Registry CLI"
        assert result["scope"] == "skills-read"

    def test_dcr_and_unknown_clients_delegate_to_store(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client("mcp-client-abc", {"client_id": "mcp-client-abc"})

        assert resolve_client_metadata("mcp-client-abc", store) == {"client_id": "mcp-client-abc"}
        assert resolve_client_metadata("unknown-client", store) is None


class TestResolveAuthorizedClientMetadata:
    def test_registry_app_uses_configured_secret(self) -> None:
        store = InMemoryOAuthStateStore()
        with patch("auth_server.services.oauth_client_policy.settings") as mocked_settings:
            mocked_settings.registry_app_name = "jarvis-registry"
            mocked_settings.registry_client_secret = "correct-secret"

            valid = resolve_authorized_client_metadata(
                "jarvis-registry", "correct-secret", "authorization_code", store, settings.jwt_token_config
            )
            invalid = resolve_authorized_client_metadata(
                "jarvis-registry", "wrong-secret", "authorization_code", store, settings.jwt_token_config
            )

        assert valid.credentials_valid is True
        assert valid.grant_authorized is True
        assert invalid.credentials_valid is False
        assert invalid.grant_authorized is False

    def test_cli_is_public_and_dcr_clients_delegate_to_store(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client(
            "mcp-client-x",
            {
                "client_secret": "s",
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )

        cli = resolve_authorized_client_metadata(
            REGISTRY_CLI_CLIENT_ID, None, DEVICE_CODE_GRANT_TYPE, store, settings.jwt_token_config
        )
        valid = resolve_authorized_client_metadata(
            "mcp-client-x", "s", "authorization_code", store, settings.jwt_token_config
        )
        invalid = resolve_authorized_client_metadata(
            "mcp-client-x", "wrong", "authorization_code", store, settings.jwt_token_config
        )
        unknown = resolve_authorized_client_metadata(
            "unknown", None, "authorization_code", store, settings.jwt_token_config
        )

        assert cli.credentials_valid is True
        assert cli.grant_authorized is True
        assert valid.credentials_valid is True
        assert valid.grant_authorized is True
        assert invalid.credentials_valid is False
        assert unknown.credentials_valid is False

    def test_cli_policy_allows_device_and_refresh_only(self) -> None:
        store = InMemoryOAuthStateStore()

        device = resolve_authorized_client_metadata(
            REGISTRY_CLI_CLIENT_ID, None, DEVICE_CODE_GRANT_TYPE, store, settings.jwt_token_config
        )
        refresh = resolve_authorized_client_metadata(
            REGISTRY_CLI_CLIENT_ID, None, "refresh_token", store, settings.jwt_token_config
        )
        authorization_code = resolve_authorized_client_metadata(
            REGISTRY_CLI_CLIENT_ID, None, "authorization_code", store, settings.jwt_token_config
        )

        assert device.grant_authorized is True
        assert refresh.grant_authorized is True
        assert authorization_code.credentials_valid is True
        assert authorization_code.grant_authorized is False

    def test_dcr_grant_requires_category_policy_and_registered_metadata(self) -> None:
        store = InMemoryOAuthStateStore()
        store.save_client("mcp-client-y", {"grant_types": ["authorization_code"]})

        authorized = resolve_authorized_client_metadata(
            "mcp-client-y", None, "authorization_code", store, settings.jwt_token_config
        )
        unauthorized = resolve_authorized_client_metadata(
            "mcp-client-y", None, DEVICE_CODE_GRANT_TYPE, store, settings.jwt_token_config
        )
        unknown = resolve_authorized_client_metadata(
            "unknown", None, "authorization_code", store, settings.jwt_token_config
        )

        assert authorized.grant_authorized is True
        assert unauthorized.credentials_valid is True
        assert unauthorized.grant_authorized is False
        assert unknown.credentials_valid is False
