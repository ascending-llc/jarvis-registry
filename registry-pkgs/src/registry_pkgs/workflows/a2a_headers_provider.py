from __future__ import annotations

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.federation.azure_foundry_client_cache import AzureFoundryClientCache
from registry_pkgs.models import A2AAgent
from registry_pkgs.workflows.a2a_client import build_headers, is_azure_foundry_runtime


class A2aHeadersProvider:
    """Build A2A authentication headers for the agent's runtime."""

    def __init__(self, *, jwt_config: JwtSigningConfig, azure_client_cache: AzureFoundryClientCache):
        self._jwt_config = jwt_config
        self._azure_client_cache = azure_client_cache

    async def __call__(self, agent: A2AAgent) -> dict[str, str]:
        if is_azure_foundry_runtime(agent):
            return await self._azure_headers(agent)
        return build_headers(agent, jwt_config=self._jwt_config)

    async def _azure_headers(self, agent: A2AAgent) -> dict[str, str]:
        federation_id = agent.federationRefId
        if federation_id is None:
            raise ValueError(
                f"Azure Foundry A2A agent {agent.path!r} has no federationRefId; "
                "cannot resolve Entra credentials for invocation"
            )
        auth = await self._azure_client_cache.get_auth_service(federation_id)
        return await auth.build_headers()


def make_a2a_headers_provider(
    *,
    jwt_config: JwtSigningConfig,
    azure_client_cache: AzureFoundryClientCache,
) -> A2aHeadersProvider:
    return A2aHeadersProvider(jwt_config=jwt_config, azure_client_cache=azure_client_cache)


__all__: list[str] = ["A2aHeadersProvider", "make_a2a_headers_provider"]
