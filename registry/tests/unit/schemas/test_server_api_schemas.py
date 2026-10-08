"""oauthMetadata in server list/detail responses comes from registryOAuth, never config.oauthMetadata (AS-1929)."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId

from registry.schemas.server_api_schemas import convert_to_detail, convert_to_list_item
from registry_pkgs.models.mcp_server_oauth import RegistryOAuthClient, RegistryOAuthState

AS_METADATA = {
    "issuer": "https://auth.atlassian.com/tenant",
    "authorization_endpoint": "https://auth.atlassian.com/authorize",
    "token_endpoint": "https://auth.atlassian.com/oauth/token",
    "registration_endpoint": "https://auth.atlassian.com/tenant/dcr/register",
}


def _server(registry_oauth: RegistryOAuthState | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        serverName="atlassian",
        path="/atlassian",
        tags=[],
        numTools=0,
        numStars=0,
        author=None,
        lastConnected=None,
        lastError=None,
        errorMessage=None,
        createdAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
        registryDisabledTools=[],
        config={
            "url": "https://mcp.atlassian.com/v2/mcp",
            "requiresOAuth": True,
            # Legacy plaintext copy written by old pods; must never be returned.
            "oauthMetadata": {"issuer": "https://auth.atlassian.com", "client_secret": "legacy-plaintext-secret"},
        },
        registryOAuth=registry_oauth,
    )


def _state() -> RegistryOAuthState:
    return RegistryOAuthState(
        revision="rev-1",
        serverUrl="https://mcp.atlassian.com/v2/mcp",
        resource="https://mcp.atlassian.com/v2/mcp",
        issuer=AS_METADATA["issuer"],
        authorizationServerMetadata=AS_METADATA,
        scope="read:me",
        discoveredAt=datetime.now(UTC),
        client=RegistryOAuthClient(
            clientId="client-1",
            clientSecret="encrypted-registry-secret",
            redirectUri="http://localhost/api/v1/mcp/atlassian/oauth/callback",
            tokenEndpointAuthMethod="client_secret_post",
            registeredAt=datetime.now(UTC),
        ),
    )


@pytest.mark.parametrize("convert", [convert_to_list_item, convert_to_detail])
def test_oauth_metadata_built_from_registry_oauth(convert) -> None:
    response = convert(_server(_state()))

    assert response.oauthMetadata == {
        "issuer": AS_METADATA["issuer"],
        "authorizationEndpoint": AS_METADATA["authorization_endpoint"],
        "tokenEndpoint": AS_METADATA["token_endpoint"],
        "registrationEndpoint": AS_METADATA["registration_endpoint"],
        "resource": "https://mcp.atlassian.com/v2/mcp",
    }


@pytest.mark.parametrize("convert", [convert_to_list_item, convert_to_detail])
def test_no_client_secret_anywhere(convert) -> None:
    dumped = json.dumps(convert(_server(_state())).model_dump(mode="json", by_alias=True))

    assert "legacy-plaintext-secret" not in dumped
    assert "encrypted-registry-secret" not in dumped
    assert "clientSecret" not in dumped and "client_secret" not in dumped
    assert "client-1" not in dumped


@pytest.mark.parametrize("convert", [convert_to_list_item, convert_to_detail])
def test_oauth_metadata_none_without_registry_oauth(convert) -> None:
    assert convert(_server(None)).oauthMetadata is None
