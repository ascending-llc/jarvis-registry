from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from registry.services.federation.agentcore_discovery import AgentCoreFederationClient
from registry.services.federation.agentcore_runtime import AgentCoreRuntimeInvoker
from registry.services.federation.agentcore_runtime_auth import AgentCoreRuntimeAuthService
from registry.services.federation.azure_ai_foundry_client import AzureAIFoundryFederationClient
from registry.services.oauth.token_service import TokenService
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import AwsAgentCoreProviderConfig, Federation

from ...core.config import settings


class BaseFederationSyncHandler(ABC):
    provider_type: FederationProviderType

    @abstractmethod
    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        raise NotImplementedError


class AwsAgentCoreSyncHandler(BaseFederationSyncHandler):
    provider_type = FederationProviderType.AWS_AGENTCORE

    def __init__(
        self,
        discovery_client: AgentCoreFederationClient | None = None,
        runtime_invoker: AgentCoreRuntimeInvoker | None = None,
        token_service: TokenService | None = None,
    ):
        self.discovery_client = discovery_client or AgentCoreFederationClient()
        self.runtime_invoker = runtime_invoker or AgentCoreRuntimeInvoker(
            client_provider=self.discovery_client.client_provider,
            extract_region_from_arn=self.discovery_client.extract_region_from_arn,
            auth_service=AgentCoreRuntimeAuthService(
                client_provider=self.discovery_client.client_provider,
                extract_region_from_arn=self.discovery_client.extract_region_from_arn,
                token_service=token_service,
            ),
        )

    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        provider_config = AwsAgentCoreProviderConfig(**dict(federation.providerConfig or {}))
        region = provider_config.region or settings.aws_region or "us-east-1"
        assume_role_arn = provider_config.assumeRoleArn
        resource_tags_filter = dict(provider_config.resourceTagsFilter or {})
        discovered = await self.discovery_client.discover_runtime_entities(
            region=region,
            author_id=None,
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
        discovery_client_factory: type[AzureAIFoundryFederationClient] = AzureAIFoundryFederationClient,
    ):
        self.discovery_client_factory = discovery_client_factory

    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        raw_config = dict(federation.providerConfig or {})
        project_endpoint = (raw_config.get("projectEndpoint") or settings.azure_ai_project_endpoint or "").strip()
        if not project_endpoint:
            raise ValueError("Azure AI Foundry federation requires providerConfig.projectEndpoint")
        metadata_filter = dict(raw_config.get("metadataFilter") or {})

        discovery_client = self.discovery_client_factory(
            project_endpoint=project_endpoint,
            metadata_filter=metadata_filter,
        )
        discovered = await discovery_client.discover_entities(author_id=None)
        return {
            "mcp_servers": [],
            "a2a_agents": discovered.get("a2a_agents", []),
            "skipped_agents": discovered.get("skipped_agents", []),
        }
