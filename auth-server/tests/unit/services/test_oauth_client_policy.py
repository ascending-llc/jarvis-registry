"""Unit tests for OAuth client metadata and authorization policy."""

from unittest.mock import Mock, patch

import pytest
from tests.support.oauth_state_store import InMemoryOAuthStateStore

from auth_server.core.config import settings
from auth_server.services.oauth_client_policy import (
    resolve_authorized_client_metadata,
    resolve_client_metadata,
)
from registry_pkgs.core.client_categories import CLAUDE_DESKTOP_CLIENT_ID, REGISTRY_CLI_CLIENT_ID
from registry_pkgs.core.downstream_oauth import DEVICE_CODE_GRANT_TYPE


class TestResolveClientMetadata:
    @pytest.mark.parametrize(
        "client_id, expected",
        [
            (
                REGISTRY_CLI_CLIENT_ID,
                {
                    "client_id": "jarvis-registry-cli",
                    "client_name": "Jarvis Registry CLI",
                    "client_secret": None,
                    "redirect_uris": [],
                    "grant_types": [DEVICE_CODE_GRANT_TYPE, "refresh_token"],
                    "response_types": [],
                    "scope": "skills-read",
                    "token_endpoint_auth_method": "none",
                },
            ),
            (
                CLAUDE_DESKTOP_CLIENT_ID,
                {
                    "client_id": "jarvis-registry-claude-desktop",
                    "client_name": "Claude Desktop",
                    "client_secret": None,
                    "redirect_uris": ["http://127.0.0.1:47280/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "scope": "mcp-proxy-ops",
                    "token_endpoint_auth_method": "none",
                },
            ),
        ],
    )
    def test_static_metadata_takes_priority_without_reading_store(
        self, client_id: str, expected: dict[str, object]
    ) -> None:
        backing_store = InMemoryOAuthStateStore()
        backing_store.save_client(client_id, {"client_name": "From Redis"})
        store = Mock(spec=InMemoryOAuthStateStore, wraps=backing_store)

        assert resolve_client_metadata(client_id, store) == expected
        assert store.mock_calls == []

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

    @pytest.mark.parametrize(
        "client_id, grant_type, expected_authorized",
        [
            (REGISTRY_CLI_CLIENT_ID, DEVICE_CODE_GRANT_TYPE, True),
            (REGISTRY_CLI_CLIENT_ID, "refresh_token", True),
            (REGISTRY_CLI_CLIENT_ID, "authorization_code", False),
            (CLAUDE_DESKTOP_CLIENT_ID, DEVICE_CODE_GRANT_TYPE, False),
            (CLAUDE_DESKTOP_CLIENT_ID, "refresh_token", True),
            (CLAUDE_DESKTOP_CLIENT_ID, "authorization_code", True),
        ],
    )
    def test_static_client_grants_without_secret_or_store_access(
        self, client_id: str, grant_type: str, expected_authorized: bool
    ) -> None:
        store = Mock(spec=InMemoryOAuthStateStore)

        result = resolve_authorized_client_metadata(client_id, None, grant_type, store, settings.jwt_token_config)

        assert result.credentials_valid is True
        assert result.grant_authorized is expected_authorized
        assert store.mock_calls == []

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
