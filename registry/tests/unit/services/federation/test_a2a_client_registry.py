from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation.a2a_client_registry import A2AClientRegistry
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import AgentCoreRuntimeJwtConfig


def _agent(
    *,
    path: str = "agent-one",
    provider: str | None = None,
    runtime_access: object | None = None,
    federation_id: PydanticObjectId | None = None,
) -> SimpleNamespace:
    metadata = {} if provider is None else {"providerType": provider}
    config = SimpleNamespace(runtimeAccess=runtime_access)
    return SimpleNamespace(
        path=path,
        federationMetadata=metadata,
        federationRefId=federation_id,
        config=config,
    )


@pytest.mark.asyncio
async def test_get_client_dispatches_azure_to_azure_cache():
    azure_cache = MagicMock()
    azure_cache.get_client = AsyncMock(return_value="azure-client")
    agentcore_registry = MagicMock()
    registry = A2AClientRegistry(agentcore_registry=agentcore_registry, azure_client_cache=azure_cache)
    agent = _agent(provider=FederationProviderType.AZURE_AI_FOUNDRY.value, federation_id=PydanticObjectId())

    client = await registry.get_client(agent)

    assert client == "azure-client"
    azure_cache.get_client.assert_awaited_once_with(agent)
    agentcore_registry.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_client_dispatches_agentcore_jwt_to_existing_registry():
    azure_cache = MagicMock()
    agentcore_registry = MagicMock()
    agentcore_registry.get.return_value = "agentcore-client"
    registry = A2AClientRegistry(agentcore_registry=agentcore_registry, azure_client_cache=azure_cache)
    jwt_config = AgentCoreRuntimeJwtConfig(audiences=["jarvis-services"])
    runtime_access = SimpleNamespace(jwt=jwt_config)
    agent = _agent(provider=FederationProviderType.AWS_AGENTCORE.value, runtime_access=runtime_access)

    client = await registry.get_client(agent)

    assert client == "agentcore-client"
    agentcore_registry.get.assert_called_once_with(
        "agent-one",
        agentcore_jwt=True,
        runtime_jwt_config=jwt_config,
    )


@pytest.mark.asyncio
async def test_get_client_dispatches_non_jwt_agentcore_to_plain_registry_path():
    azure_cache = MagicMock()
    agentcore_registry = MagicMock()
    agentcore_registry.get.return_value = "plain-agentcore-client"
    registry = A2AClientRegistry(agentcore_registry=agentcore_registry, azure_client_cache=azure_cache)
    agent = _agent(provider=FederationProviderType.AWS_AGENTCORE.value, runtime_access=None)

    client = await registry.get_client(agent)

    assert client == "plain-agentcore-client"
    agentcore_registry.get.assert_called_once_with(
        "agent-one",
        agentcore_jwt=False,
        runtime_jwt_config=None,
    )


@pytest.mark.asyncio
async def test_get_client_dispatches_unfederated_agent_to_plain_registry_path():
    azure_cache = MagicMock()
    agentcore_registry = MagicMock()
    agentcore_registry.get.return_value = "plain-client"
    registry = A2AClientRegistry(agentcore_registry=agentcore_registry, azure_client_cache=azure_cache)
    agent = _agent(provider=None, runtime_access=None)

    client = await registry.get_client(agent)

    assert client == "plain-client"
    agentcore_registry.get.assert_called_once_with(
        "agent-one",
        agentcore_jwt=False,
        runtime_jwt_config=None,
    )


@pytest.mark.asyncio
async def test_close_closes_composed_registries():
    azure_cache = MagicMock()
    azure_cache.close = AsyncMock()
    agentcore_registry = MagicMock()
    agentcore_registry.close = AsyncMock()
    registry = A2AClientRegistry(agentcore_registry=agentcore_registry, azure_client_cache=azure_cache)

    await registry.close()

    agentcore_registry.close.assert_awaited_once()
    azure_cache.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidate_azure_federation_delegates_to_cache():
    federation_id = PydanticObjectId()
    azure_cache = MagicMock()
    azure_cache.invalidate = AsyncMock()
    registry = A2AClientRegistry(agentcore_registry=MagicMock(), azure_client_cache=azure_cache)

    await registry.invalidate_azure_federation(federation_id)

    azure_cache.invalidate.assert_awaited_once_with(federation_id)
