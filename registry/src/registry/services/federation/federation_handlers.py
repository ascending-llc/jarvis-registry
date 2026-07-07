from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from beanie import PydanticObjectId

from registry.services.federation.agentcore_discovery import AgentCoreFederationClient
from registry.services.federation.agentcore_runtime import AgentCoreRuntimeInvoker
from registry.services.federation.azure_foundry_auth import AzureFoundryAuthService
from registry.services.federation.azure_foundry_discovery import AzureFoundryDiscoveryClient
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import (
    AwsAgentCoreProviderConfig,
    AzureAiFoundryProviderConfig,
    Federation,
)

from ...core.config import settings


class BaseFederationSyncHandler(ABC):
    provider_type: FederationProviderType

    @abstractmethod
    async def discover_entities(
        self,
        federation: Federation,
        *,
        author_id: PydanticObjectId,
    ) -> dict[str, list[Any]]:
        raise NotImplementedError


class AwsAgentCoreSyncHandler(BaseFederationSyncHandler):
    provider_type = FederationProviderType.AWS_AGENTCORE

    def __init__(
        self,
        discovery_client: AgentCoreFederationClient | None = None,
        runtime_invoker: AgentCoreRuntimeInvoker | None = None,
    ):
        self.discovery_client = discovery_client or AgentCoreFederationClient()
        self.runtime_invoker = runtime_invoker or AgentCoreRuntimeInvoker(
            client_provider=self.discovery_client.client_provider,
            extract_region_from_arn=self.discovery_client.extract_region_from_arn,
        )

    async def discover_entities(
        self,
        federation: Federation,
        *,
        author_id: PydanticObjectId,
    ) -> dict[str, list[Any]]:
        provider_config = AwsAgentCoreProviderConfig(**dict(federation.providerConfig or {}))
        region = provider_config.region or settings.aws_region or "us-east-1"
        assume_role_arn = provider_config.assumeRoleArn
        resource_tags_filter = dict(provider_config.resourceTagsFilter or {})
        discovered = await self.discovery_client.discover_runtime_entities(
            region=region,
            author_id=author_id,
            assume_role_arn=assume_role_arn,
            resource_tags_filter=resource_tags_filter,
        )
        await self._enrich_discovered_entities(
            federation=federation,
            discovered=discovered,
            region=region,
            assume_role_arn=assume_role_arn,
        )
        return discovered

    async def _enrich_discovered_entities(
        self,
        federation: Federation,
        discovered: dict[str, list[Any]],
        *,
        region: str,
        assume_role_arn: str | None,
    ) -> None:
        # Runtime enrichment needs the federation context because JWT mode is a
        # federation-level decision, not something stored on each child entity.
        for server in discovered.get("mcp_servers", []):
            await self.runtime_invoker.enrich_mcp_server(
                server=server,
                federation=federation,
                region=region,
                assume_role_arn=assume_role_arn,
            )

        for agent in discovered.get("a2a_agents", []):
            await self.runtime_invoker.enrich_a2a_agent(
                agent=agent,
                federation=federation,
                runtime_detail=dict(agent.federationMetadata or {}),
                region=region,
                assume_role_arn=assume_role_arn,
            )


class AzureAiFoundrySyncHandler(BaseFederationSyncHandler):
    provider_type = FederationProviderType.AZURE_AI_FOUNDRY

    def __init__(
        self,
        discovery_client: AzureFoundryDiscoveryClient | None = None,
    ):
        self.discovery_client = discovery_client or AzureFoundryDiscoveryClient()

    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        provider_config = AzureAiFoundryProviderConfig(**dict(federation.providerConfig or {}))
        async with AzureFoundryAuthService(provider_config) as auth:
            agents = await self.discovery_client.discover_a2a_agents(
                provider_config=provider_config,
                auth=auth,
            )
        # Foundry hosted agents only expose A2A;
        return {"a2a_agents": agents, "mcp_servers": []}
