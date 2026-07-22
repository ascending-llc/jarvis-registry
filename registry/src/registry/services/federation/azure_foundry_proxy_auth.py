from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx
from beanie import PydanticObjectId

from registry_pkgs.models import A2AAgent
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig, Federation

from .azure_foundry_auth import AzureFoundryAuthService

logger = logging.getLogger(__name__)


class AzureEntraAuth(httpx.Auth):
    """httpx auth hook that injects Azure Entra headers for each outgoing request."""

    def __init__(self, auth_service: AzureFoundryAuthService):
        self._auth_service = auth_service

    async def async_auth_flow(self, request: httpx.Request) -> AsyncIterator[httpx.Request]:
        headers = await self._auth_service.build_headers()
        for key, value in headers.items():
            request.headers[key] = value
        yield request

    async def close(self) -> None:
        await self._auth_service.close()


class AzureFoundryClientCache:
    """Cache one Azure-authenticated A2A client per federation."""

    def __init__(self):
        self._dict: dict[PydanticObjectId, httpx.AsyncClient] = {}
        self._locks: dict[PydanticObjectId, asyncio.Lock] = {}

    async def get_client(self, agent: A2AAgent) -> httpx.AsyncClient:
        federation_id = agent.federationRefId
        if federation_id is None:
            raise ValueError(f"Azure Foundry A2A agent {agent.path!r} has no federationRefId")

        cached = self._dict.get(federation_id)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            cached = self._dict.get(federation_id)
            if cached is not None:
                return cached

            federation = await Federation.get(federation_id)
            if federation is None:
                raise ValueError(f"Federation {federation_id} not found")

            if federation.providerType != FederationProviderType.AZURE_AI_FOUNDRY:
                raise ValueError(
                    f"Federation {federation_id} providerType={federation.providerType!r} is not azure_ai_foundry"
                )

            cfg = AzureAiFoundryProviderConfig(**(federation.providerConfig or {}))
            auth_service = AzureFoundryAuthService(cfg)
            client = httpx.AsyncClient(
                auth=AzureEntraAuth(auth_service),
                timeout=httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
            self._dict[federation_id] = client
            return client

    async def invalidate(self, federation_id: PydanticObjectId) -> None:
        """Drop a federation's cached client and close its resources.

        This prevents connection/credential leaks when a federation is updated
        and its pre-authenticated client must be rebuilt from fresh config.

        Coordinates through the same per-federation lock `get_client` uses, so
        this always waits for an in-flight build to finish (evicting the client
        it just stored, not a stale one) before it clears the cache, and can
        never race the next `get_client` call into building on a second,
        independent lock.
        """
        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            client = self._dict.pop(federation_id, None)
            if client is not None:
                auth = client.auth
                await client.aclose()
                if isinstance(auth, AzureEntraAuth):
                    await auth.close()

    async def close(self) -> None:
        clients = list(self._dict.values())
        self._dict.clear()
        self._locks.clear()

        for client in clients:
            auth = client.auth
            await client.aclose()
            if isinstance(auth, AzureEntraAuth):
                await auth.close()


__all__: list[str] = ["AzureEntraAuth", "AzureFoundryClientCache"]
