from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from beanie import PydanticObjectId

from registry.services.federation.azure_foundry_proxy_auth import AzureEntraAuth, AzureFoundryClientCache
from registry_pkgs.models.enums import FederationProviderType


def _agent(*, federation_id: PydanticObjectId | None) -> SimpleNamespace:
    return SimpleNamespace(
        federationRefId=federation_id,
        path="test-agent",
    )


def _federation(provider_type: str, provider_config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        providerType=provider_type,
        providerConfig=provider_config
        or {
            "projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p",
            "tenantId": "tenant",
            "clientId": "client",
            "clientSecret": "plain-secret",
        },
    )


@pytest.mark.asyncio
async def test_azure_entra_auth_injects_headers():
    auth_service = MagicMock()
    auth_service.build_headers = AsyncMock(
        return_value={
            "Authorization": "Bearer entra-token",
            "Foundry-Features": "HostedAgents=V1Preview",
        }
    )
    auth = AzureEntraAuth(auth_service)
    request = httpx.Request("POST", "https://agent.example.com", headers={"Authorization": "Bearer caller-token"})

    flow = auth.async_auth_flow(request)
    authed_request = await anext(flow)

    assert authed_request.headers["Authorization"] == "Bearer entra-token"
    assert authed_request.headers["Foundry-Features"] == "HostedAgents=V1Preview"
    auth_service.build_headers.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_client_cache_hit_reuses_client_without_db_or_auth_rebuild():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    cache = AzureFoundryClientCache()
    federation_get = AsyncMock(return_value=federation)
    auth_factory = MagicMock()

    with (
        patch("registry.services.federation.azure_foundry_proxy_auth.Federation.get", new=federation_get),
        patch("registry.services.federation.azure_foundry_proxy_auth.AzureFoundryAuthService", new=auth_factory),
    ):
        client = await cache.get_client(_agent(federation_id=federation_id))
        same_client = await cache.get_client(_agent(federation_id=federation_id))

    try:
        assert same_client is client
        assert federation_get.await_count == 1
        assert auth_factory.call_count == 1
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_invalidate_forces_rebuild():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    cache = AzureFoundryClientCache()

    with patch(
        "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
        new=AsyncMock(return_value=federation),
    ):
        client = await cache.get_client(_agent(federation_id=federation_id))
        cache.invalidate(federation_id)
        rebuilt_client = await cache.get_client(_agent(federation_id=federation_id))

    try:
        assert rebuilt_client is not client
    finally:
        await client.aclose()
        await cache.close()


@pytest.mark.asyncio
async def test_close_closes_clients_and_auth_services():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    auth_service = MagicMock()
    auth_service.close = AsyncMock()
    cache = AzureFoundryClientCache()

    with (
        patch(
            "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
            new=AsyncMock(return_value=federation),
        ),
        patch(
            "registry.services.federation.azure_foundry_proxy_auth.AzureFoundryAuthService",
            return_value=auth_service,
        ),
    ):
        client = await cache.get_client(_agent(federation_id=federation_id))
        await cache.close()

    assert client.is_closed
    auth_service.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_azure_agent_without_federation_ref_raises():
    cache = AzureFoundryClientCache()

    with pytest.raises(ValueError, match="federationRefId"):
        await cache.get_client(_agent(federation_id=None))


@pytest.mark.asyncio
async def test_missing_federation_raises():
    cache = AzureFoundryClientCache()

    with patch(
        "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="not found"):
            await cache.get_client(_agent(federation_id=PydanticObjectId()))


@pytest.mark.asyncio
async def test_wrong_provider_type_federation_raises():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AWS_AGENTCORE.value, provider_config={})
    federation.id = federation_id
    cache = AzureFoundryClientCache()

    with patch(
        "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
        new=AsyncMock(return_value=federation),
    ):
        with pytest.raises(ValueError, match="is not azure_ai_foundry"):
            await cache.get_client(_agent(federation_id=federation_id))
