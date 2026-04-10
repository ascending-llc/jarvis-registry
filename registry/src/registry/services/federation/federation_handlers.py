from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from registry.services.federation.agentcore_discovery import AgentCoreFederationClient
from registry.services.federation.agentcore_runtime import AgentCoreRuntimeInvoker
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import Federation

from ...core.config import settings


class BaseFederationSyncHandler(ABC):
    provider_type: FederationProviderType

    @abstractmethod
    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        raise NotImplementedError


@dataclass(frozen=True)
class AwsAgentCoreConnectionConfig:
    """
    Canonical AWS connection settings for federation sync.

    Boundary rule:
    - `providerConfig` remains camelCase because it mirrors API/storage shape.
    - Service-layer execution uses snake_case only.
    """

    region: str
    assume_role_arn: str | None
    resource_tags_filter: dict[str, str]

    @classmethod
    def from_provider_config(cls, provider_config: dict[str, Any] | None) -> AwsAgentCoreConnectionConfig:
        # Normalize API/storage camelCase once at the handler boundary so the
        # rest of the sync path can stay in snake_case.
        raw_config = dict(provider_config or {})
        return cls(
            region=raw_config.get("region") or settings.aws_region or "us-east-1",
            assume_role_arn=raw_config.get("assumeRoleArn"),
            resource_tags_filter=dict(raw_config.get("resourceTagsFilter") or {}),
        )


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

    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        connection = AwsAgentCoreConnectionConfig.from_provider_config(federation.providerConfig)
        discovered = await self.discovery_client.discover_runtime_entities(
            region=connection.region,
            author_id=None,
            assume_role_arn=connection.assume_role_arn,
            resource_tags_filter=connection.resource_tags_filter,
        )
        await self._enrich_discovered_entities(
            federation=federation,
            discovered=discovered,
            region=connection.region,
            assume_role_arn=connection.assume_role_arn,
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

    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        raise ValueError(
            "Federation provider azure_ai_foundry is not implemented yet. "
            "The sync handler hook is ready; only the Azure discovery adapter is pending."
        )
