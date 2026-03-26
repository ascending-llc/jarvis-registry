from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import Federation

from ...core.config import settings
from .agentcore_client import AgentCoreFederationClient
from .agentcore_client_provider import AgentCoreClientProvider
from .runtime_invoker import AgentCoreRuntimeInvoker


class BaseFederationSyncHandler(ABC):
    provider_type: FederationProviderType

    @abstractmethod
    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        raise NotImplementedError


class AwsAgentCoreSyncHandler(BaseFederationSyncHandler):
    provider_type = FederationProviderType.AWS_AGENTCORE

    def build_client(self, federation: Federation) -> AgentCoreFederationClient:
        # Region and assumeRoleArn are federation-level connection settings.
        # They control how this federation talks to the AgentCore control plane
        # and should not be duplicated onto child MCP/A2A resources.
        provider_config = dict(federation.providerConfig or {})
        region = provider_config.get("region") or settings.aws_region or "us-east-1"
        assume_role_arn = provider_config.get("assumeRoleArn")

        client_provider = AgentCoreClientProvider(
            default_region=region,
            assume_role_arn=assume_role_arn,
        )
        runtime_invoker = AgentCoreRuntimeInvoker(
            default_region=client_provider.default_region,
            get_runtime_client=client_provider.get_runtime_client,
            get_runtime_credentials_provider=client_provider.get_runtime_credentials_provider,
            extract_region_from_arn=AgentCoreFederationClient.extract_region_from_arn,
        )
        return AgentCoreFederationClient(
            region=region,
            client_provider=client_provider,
            runtime_invoker=runtime_invoker,
        )

    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        # Federation sync without an explicit runtime selector performs a
        # full discovery for the resolved region only.
        client = self.build_client(federation)
        provider_config = dict(federation.providerConfig or {})
        return await client.discover_runtime_entities(
            author_id=None,
            resource_tags_filter=dict(provider_config.get("resourceTagsFilter") or {}),
        )


class AzureAiFoundrySyncHandler(BaseFederationSyncHandler):
    provider_type = FederationProviderType.AZURE_AI_FOUNDRY

    async def discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        raise ValueError(
            "Federation provider azure_ai_foundry is not implemented yet. "
            "The sync handler hook is ready; only the Azure discovery adapter is pending."
        )
