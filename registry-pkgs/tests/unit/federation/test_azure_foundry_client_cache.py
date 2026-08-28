from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from beanie import PydanticObjectId

from registry_pkgs.federation.azure_foundry_client_cache import AzureEntraAuth, AzureFoundryClientCache
from registry_pkgs.models.enums import FederationProviderType

_KEY = b"0" * 32
_FED_GET = "registry_pkgs.federation.azure_foundry_client_cache.Federation.get"
_AUTH_SVC = "registry_pkgs.federation.azure_foundry_client_cache.AzureFoundryAuthService"


def _cache() -> AzureFoundryClientCache:
    return AzureFoundryClientCache(encryption_key=_KEY)


def _agent(*, federation_id: PydanticObjectId | None) -> SimpleNamespace:
    return SimpleNamespace(federationRefId=federation_id, path="test-agent")


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
async def test_get_auth_service_caches_identical_instance():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    federation_get = AsyncMock(return_value=federation)
    auth_factory = MagicMock()
    auth_factory.return_value.close = AsyncMock()

    cache = _cache()
    with patch(_FED_GET, new=federation_get), patch(_AUTH_SVC, new=auth_factory):
        first = await cache.get_auth_service(federation_id)
        second = await cache.get_auth_service(federation_id)

    try:
        assert first is second
        assert federation_get.await_count == 1
        assert auth_factory.call_count == 1
        _, kwargs = auth_factory.call_args
        assert kwargs["encryption_key"] == _KEY
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_get_client_reuses_shared_auth_service():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    federation_get = AsyncMock(return_value=federation)
    auth_factory = MagicMock()
    auth_factory.return_value.close = AsyncMock()

    cache = _cache()
    with patch(_FED_GET, new=federation_get), patch(_AUTH_SVC, new=auth_factory):
        auth = await cache.get_auth_service(federation_id)
        client = await cache.get_client(_agent(federation_id=federation_id))
        same_client = await cache.get_client(_agent(federation_id=federation_id))

    try:
        assert same_client is client
        assert federation_get.await_count == 1
        assert auth_factory.call_count == 1
        assert client.auth._auth_service is auth
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_invalidate_rebuilds_auth_service_and_client():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    auth_factory = MagicMock(side_effect=lambda *a, **k: MagicMock(close=AsyncMock()))

    cache = _cache()
    with patch(_FED_GET, new=AsyncMock(return_value=federation)), patch(_AUTH_SVC, new=auth_factory):
        first_auth = await cache.get_auth_service(federation_id)
        client = await cache.get_client(_agent(federation_id=federation_id))
        await cache.invalidate(federation_id)
        second_auth = await cache.get_auth_service(federation_id)
        rebuilt_client = await cache.get_client(_agent(federation_id=federation_id))

    try:
        assert second_auth is not first_auth
        assert rebuilt_client is not client
        assert client.is_closed
        first_auth.close.assert_awaited()
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_close_closes_clients_and_auth_services():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    auth_service = MagicMock()
    auth_service.close = AsyncMock()

    cache = _cache()
    with patch(_FED_GET, new=AsyncMock(return_value=federation)), patch(_AUTH_SVC, return_value=auth_service):
        client = await cache.get_client(_agent(federation_id=federation_id))
        await cache.close()

    assert client.is_closed
    auth_service.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_azure_agent_without_federation_ref_raises():
    cache = _cache()
    with pytest.raises(ValueError, match="federationRefId"):
        await cache.get_client(_agent(federation_id=None))


@pytest.mark.asyncio
async def test_missing_federation_raises():
    cache = _cache()
    with patch(_FED_GET, new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="not found"):
            await cache.get_auth_service(PydanticObjectId())


@pytest.mark.asyncio
async def test_wrong_provider_type_federation_raises():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AWS_AGENTCORE.value, provider_config={})
    federation.id = federation_id
    cache = _cache()

    with patch(_FED_GET, new=AsyncMock(return_value=federation)):
        with pytest.raises(ValueError, match="is not azure_ai_foundry"):
            await cache.get_auth_service(federation_id)


@pytest.mark.asyncio
async def test_invalidate_cannot_interleave_get_client_build():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    build_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_get(_federation_id):
        build_started.set()
        await release.wait()
        return federation

    auth_factory = MagicMock(side_effect=lambda *a, **k: MagicMock(close=AsyncMock()))
    cache = _cache()
    with patch(_FED_GET, side_effect=slow_get), patch(_AUTH_SVC, new=auth_factory):
        get_task = asyncio.create_task(cache.get_client(_agent(federation_id=federation_id)))
        await build_started.wait()
        inv_task = asyncio.create_task(cache.invalidate(federation_id))
        await asyncio.sleep(0)
        assert not inv_task.done()
        release.set()
        client = await get_task
        await inv_task

    assert client.is_closed
    assert federation_id not in cache._clients
    assert federation_id not in cache._auth_services
    await cache.close()


@pytest.mark.asyncio
async def test_concurrent_get_auth_service_builds_only_one():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id
    build_count = 0

    async def slow_federation_get(_federation_id):
        nonlocal build_count
        build_count += 1
        await asyncio.sleep(0)
        return federation

    auth_factory = MagicMock()
    auth_factory.return_value.close = AsyncMock()
    cache = _cache()
    with patch(_FED_GET, side_effect=slow_federation_get), patch(_AUTH_SVC, new=auth_factory):
        auth_a, auth_b = await asyncio.gather(
            cache.get_auth_service(federation_id),
            cache.get_auth_service(federation_id),
        )

    try:
        assert auth_a is auth_b
        assert build_count == 1
        assert auth_factory.call_count == 1
    finally:
        await cache.close()
