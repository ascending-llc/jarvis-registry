from __future__ import annotations

import logging

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models import A2AAgent
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig, Federation
from registry_pkgs.workflows.a2a_client import (
    build_headers,
    is_azure_foundry_runtime,
)

from .azure_foundry_auth import AzureFoundryAuthService

logger = logging.getLogger(__name__)


class A2aHeadersProvider:
    """Build per-call A2A auth headers, dispatching on the agent's provider type.

    Use as the `headers_provider` callback for `call_a2a` / `build_executor_registry`.
    """

    def __init__(self, *, jwt_config: JwtSigningConfig):
        self._jwt_config = jwt_config

    async def __call__(self, agent: A2AAgent) -> dict[str, str]:
        if is_azure_foundry_runtime(agent):
            return await self._azure_headers(agent)
        # Non-Azure agents fall back to the self-signed JWT path that
        # AWS AgentCore + plain A2A agents already rely on.
        return build_headers(agent, jwt_config=self._jwt_config)

    async def _azure_headers(self, agent: A2AAgent) -> dict[str, str]:
        federation_id = agent.federationRefId
        if federation_id is None:
            raise ValueError(
                f"Azure Foundry A2A agent {agent.path!r} has no federationRefId; "
                "cannot resolve Entra credentials for invocation"
            )

        federation = await Federation.get(federation_id)
        if federation is None:
            raise ValueError(f"Federation {federation_id} not found while resolving Azure A2A headers")

        azure = FederationProviderType.AZURE_AI_FOUNDRY
        if federation.providerType not in (azure, getattr(azure, "value", None)):
            raise ValueError(
                f"Federation {federation_id} providerType={federation.providerType!r} is not azure_ai_foundry"
            )

        cfg = AzureAiFoundryProviderConfig(**(federation.providerConfig or {}))
        async with AzureFoundryAuthService(cfg) as auth:
            return await auth.build_headers()


def make_a2a_headers_provider(*, jwt_config: JwtSigningConfig) -> A2aHeadersProvider:
    """Factory used by the DI container."""
    return A2aHeadersProvider(jwt_config=jwt_config)


__all__: list[str] = ["A2aHeadersProvider", "make_a2a_headers_provider"]
