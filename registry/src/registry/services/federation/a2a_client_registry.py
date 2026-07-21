from __future__ import annotations

from typing import Any

import httpx
from beanie import PydanticObjectId

from registry.core.a2a_proxy import A2AProxyClientRegistry
from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import AgentCoreRuntimeJwtConfig
from registry_pkgs.workflows.a2a_client import is_azure_foundry_runtime

from .azure_foundry_proxy_auth import AzureFoundryClientCache


def _is_agentcore_jwt(
    agent_config: AgentConfig | None,
    federation_metadata: dict[str, Any] | None,
) -> bool:
    fed = federation_metadata or {}
    return (
        agent_config is not None
        and agent_config.runtimeAccess is not None
        and fed.get("providerType") == FederationProviderType.AWS_AGENTCORE
    )


def _get_agentcore_runtime_jwt_config(agent_config: AgentConfig | None) -> AgentCoreRuntimeJwtConfig | None:
    if agent_config is None or agent_config.runtimeAccess is None:
        return None
    return agent_config.runtimeAccess.jwt


class A2AClientRegistry:
    """Resolve the correctly authenticated httpx client for an A2A agent."""

    def __init__(
        self,
        *,
        agentcore_registry: A2AProxyClientRegistry,
        azure_client_cache: AzureFoundryClientCache,
    ):
        self._agentcore_registry = agentcore_registry
        self._azure_client_cache = azure_client_cache

    async def get_client(self, agent: A2AAgent) -> httpx.AsyncClient:
        if is_azure_foundry_runtime(agent):
            return await self._azure_client_cache.get_client(agent)

        agentcore_jwt = _is_agentcore_jwt(agent.config, agent.federationMetadata)
        return self._agentcore_registry.get(
            agent.path,
            agentcore_jwt=agentcore_jwt,
            runtime_jwt_config=_get_agentcore_runtime_jwt_config(agent.config) if agentcore_jwt else None,
        )

    def invalidate_azure_federation(self, federation_id: PydanticObjectId) -> None:
        self._azure_client_cache.invalidate(federation_id)

    async def close(self) -> None:
        await self._agentcore_registry.close()
        await self._azure_client_cache.close()
