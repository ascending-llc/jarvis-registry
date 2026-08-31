from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from beanie import PydanticObjectId

from registry.services.federation.agentcore_discovery import AgentCoreFederationClient
from registry.services.federation.agentcore_runtime import AgentCoreRuntimeInvoker
from registry.services.federation.azure_foundry_discovery import AzureFoundryDiscoveryClient
from registry_pkgs.federation.azure_foundry_client_cache import AzureFoundryClientCache
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import (
    AwsAgentCoreProviderConfig,
    AzureAiFoundryProviderConfig,
    Federation,
)

from ...core.config import settings
from ...utils.concurrency import run_bounded

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _EnrichmentItem:
    kind: str
    entity: Any


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
        items = [
            *(_EnrichmentItem(kind="mcp", entity=server) for server in discovered.get("mcp_servers", [])),
            *(_EnrichmentItem(kind="a2a", entity=agent) for agent in discovered.get("a2a_agents", [])),
        ]

        async def _enrich_one(item: _EnrichmentItem) -> None:
            if item.kind == "mcp":
                await self.runtime_invoker.enrich_mcp_server(
                    server=item.entity,
                    federation=federation,
                    region=region,
                    assume_role_arn=assume_role_arn,
                )
                return

            agent = item.entity
            await self.runtime_invoker.enrich_a2a_agent(
                agent=agent,
                federation=federation,
                runtime_detail=(
                    agent.federationMetadata.model_dump(mode="json", exclude_none=True)
                    if agent.federationMetadata
                    else {}
                ),
                region=region,
                assume_role_arn=assume_role_arn,
            )

        outcomes = await run_bounded(
            items,
            _enrich_one,
            limit=settings.federation_enrichment_max_concurrency,
        )
        for outcome in outcomes:
            if outcome.ok:
                continue
            logger.error(
                "Unexpected %s federation enrichment failure: %s",
                outcome.item.kind,
                outcome.error,
                exc_info=outcome.exc_info,
            )


class AzureAiFoundrySyncHandler(BaseFederationSyncHandler):
    provider_type = FederationProviderType.AZURE_AI_FOUNDRY

    def __init__(
        self,
        azure_client_cache: AzureFoundryClientCache,
        discovery_client: AzureFoundryDiscoveryClient | None = None,
    ):
        self._azure_client_cache = azure_client_cache
        self.discovery_client = discovery_client or AzureFoundryDiscoveryClient()

    async def discover_entities(
        self,
        federation: Federation,
        *,
        author_id: PydanticObjectId,
    ) -> dict[str, list[Any]]:
        provider_config = AzureAiFoundryProviderConfig(**dict(federation.providerConfig or {}))
        auth = await self._azure_client_cache.get_auth_service(federation.id)
        agents = await self.discovery_client.discover_a2a_agents(
            provider_config=provider_config,
            auth=auth,
            author_id=author_id,
        )
        # Foundry hosted agents only expose A2A;
        return {"a2a_agents": agents, "mcp_servers": []}
