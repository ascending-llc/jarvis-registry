from __future__ import annotations

import asyncio
import logging

from beanie import PydanticObjectId

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
        self._azure_auth_cache: dict[str, AzureFoundryAuthService] = {}
        self._cache_lock = asyncio.Lock()

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

        auth = await self._get_or_create_azure_auth(federation_id)
        return await auth.build_headers()

    async def _get_or_create_azure_auth(self, federation_id: PydanticObjectId) -> AzureFoundryAuthService:
        cache_key = str(federation_id)
        cached = self._azure_auth_cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._cache_lock:
            # Re-check under the lock; another caller may have populated the slot.
            cached = self._azure_auth_cache.get(cache_key)
            if cached is not None:
                return cached

            federation = await Federation.get(federation_id)
            if federation is None:
                raise ValueError(f"Federation {federation_id} not found while resolving Azure A2A headers")
            if federation.providerType != FederationProviderType.AZURE_AI_FOUNDRY and (
                federation.providerType != getattr(FederationProviderType.AZURE_AI_FOUNDRY, "value", None)
            ):
                raise ValueError(
                    f"Federation {federation_id} providerType={federation.providerType!r} is not azure_ai_foundry"
                )
            cfg = AzureAiFoundryProviderConfig(**(federation.providerConfig or {}))
            auth = AzureFoundryAuthService(cfg)
            self._azure_auth_cache[cache_key] = auth
            return auth

    async def close(self) -> None:
        """Release cached credentials. Call from app shutdown."""
        services = list(self._azure_auth_cache.values())
        self._azure_auth_cache.clear()
        for service in services:
            try:
                await service.close()
            except Exception as exc:
                logger.warning("Failed to close cached AzureFoundryAuthService: %s", exc)

    def invalidate(self, federation_id: PydanticObjectId | str) -> None:
        """Drop a cached credential — e.g. after the federation's providerConfig was updated."""
        cache_key = str(federation_id)
        cached = self._azure_auth_cache.pop(cache_key, None)
        if cached is not None:
            # Best-effort schedule of cleanup so callers don't need an event loop.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(cached.close())
            except RuntimeError:
                # No running loop (called from sync context); the GC will close.
                pass


def make_a2a_headers_provider(*, jwt_config: JwtSigningConfig) -> A2aHeadersProvider:
    """Factory used by the DI container."""
    return A2aHeadersProvider(jwt_config=jwt_config)


__all__: list[str] = ["A2aHeadersProvider", "make_a2a_headers_provider"]
