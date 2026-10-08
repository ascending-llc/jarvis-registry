"""registryOAuth must be readable through the model but never written by Beanie (AS-1929).

ExtendedMCPServer has keep_nulls=False, so save()/save_changes() $set every non-None field and
$unset every top-level None field. If registryOAuth were part of either payload, a save of a
document loaded before (or at an older revision than) the latest registryOAuth write would remove
it or roll it back. These tests guard against a Beanie upgrade that stops honoring Pydantic's
``exclude``.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from beanie.odm.settings.document import DocumentSettings
from beanie.odm.utils.dump import get_dict, get_top_level_nones

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.mcp_server_oauth import RegistryOAuthClient, RegistryOAuthState

STORED_STATE = {
    "revision": "rev-1",
    "serverUrl": "https://mcp.example.com/mcp",
    "resource": "https://mcp.example.com/mcp",
    "issuer": "https://as.example.com",
    "protectedResourceMetadata": None,
    "authorizationServerMetadata": {"issuer": "https://as.example.com"},
    "scope": "read",
    "discoveredAt": datetime(2026, 10, 1, tzinfo=UTC),
    "client": {
        "clientId": "client-1",
        "clientSecret": "encrypted-secret",
        "redirectUri": "http://localhost/api/v1/mcp/example/oauth/callback",
        "tokenEndpointAuthMethod": "client_secret_post",
        "scope": "read",
        "registeredAt": datetime(2026, 10, 1, tzinfo=UTC),
        "clientSecretExpiresAt": None,
    },
}


def _document(**extra: object) -> dict[str, object]:
    return {
        "_id": "65f000000000000000000001",
        "serverName": "example",
        "normalizedServerName": "example",
        "author": "65f000000000000000000002",
        "config": {"url": "https://mcp.example.com/mcp", "requiresOAuth": True},
        **extra,
    }


@pytest.fixture(autouse=True)
def _settings() -> Iterator[None]:
    # Beanie needs collection settings; mirror the model's write-relevant ones without a database.
    with patch.object(
        ExtendedMCPServer,
        "get_settings",
        return_value=DocumentSettings(keep_nulls=False, use_state_management=True),
    ):
        yield


def test_registry_oauth_populated_on_read() -> None:
    server = ExtendedMCPServer.model_validate(_document(registryOAuth=STORED_STATE))

    assert isinstance(server.registryOAuth, RegistryOAuthState)
    assert isinstance(server.registryOAuth.client, RegistryOAuthClient)
    assert server.registryOAuth.client.clientId == "client-1"


def test_registry_oauth_absent_from_model_dump() -> None:
    server = ExtendedMCPServer.model_validate(_document(registryOAuth=STORED_STATE))

    assert "registryOAuth" not in server.model_dump()
    assert "encrypted-secret" not in str(server.model_dump())


def test_save_set_payload_excludes_registry_oauth() -> None:
    server = ExtendedMCPServer.model_validate(_document(registryOAuth=STORED_STATE))

    payload = get_dict(server, to_db=True, keep_nulls=False)

    assert "registryOAuth" not in payload


def test_save_unset_payload_excludes_registry_oauth_when_none() -> None:
    # A document loaded before the first login has registryOAuth=None in memory.
    server = ExtendedMCPServer.model_validate(_document())
    assert server.registryOAuth is None

    assert "registryOAuth" not in get_top_level_nones(server)
    assert "registryOAuth" not in get_dict(server, to_db=True, keep_nulls=False)


def test_is_bound_to_compares_server_url() -> None:
    state = RegistryOAuthState.model_validate(STORED_STATE)

    assert state.is_bound_to("https://mcp.example.com/mcp")
    assert not state.is_bound_to("https://mcp.example.com/v2/mcp")
