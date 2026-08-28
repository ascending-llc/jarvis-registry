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


class AzureFoundryClientCache:
    """Single, shared source of Azure Foundry credentials per federation.

    Caches both the ``AzureFoundryAuthService`` (the credential) and the pooled
    ``httpx.AsyncClient`` built on top of it. ``get_auth_service`` is the one place a
    credential is built, so every consumer (workflow A2A headers, federation sync,
    agent-card re-discovery, the direct-proxy client) reuses the same instance.
    """

    def __init__(
        self,
        *,
        encryption_key: bytes,
        max_connections: int = 300,
        max_keepalive_connections: int = 20,
    ):
        self._encryption_key = encryption_key
        self._auth_services: dict[PydanticObjectId, AzureFoundryAuthService] = {}
        self._clients: dict[PydanticObjectId, httpx.AsyncClient] = {}
        self._locks: dict[PydanticObjectId, asyncio.Lock] = {}
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )

    async def _get_auth_service_locked(self, federation_id: PydanticObjectId) -> AzureFoundryAuthService:
        """Build or return the cached auth service. Caller MUST hold this federation's lock.

        Kept lock-free so ``get_client`` can build the auth service and the client under a
        single lock acquisition — otherwise an ``invalidate`` could slip between the two and
        leave a client cached on a just-evicted (stale-config) credential.
        """
        cached = self._auth_services.get(federation_id)
        if cached is not None:
            logger.info(f"Using cached auth service for {federation_id}")
            return cached

        federation = await Federation.get(federation_id)
        if federation is None:
            raise ValueError(f"Federation {federation_id} not found")

        if federation.providerType != FederationProviderType.AZURE_AI_FOUNDRY:
            raise ValueError(
                f"Federation {federation_id} providerType={federation.providerType!r} is not azure_ai_foundry"
            )

        cfg = AzureAiFoundryProviderConfig(**(federation.providerConfig or {}))
        auth_service = AzureFoundryAuthService(cfg, encryption_key=self._encryption_key)
        self._auth_services[federation_id] = auth_service
        return auth_service

    async def get_auth_service(self, federation_id: PydanticObjectId) -> AzureFoundryAuthService:
        """Return the cached AzureFoundryAuthService for a federation, building it on first use."""
        cached = self._auth_services.get(federation_id)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            return await self._get_auth_service_locked(federation_id)

    async def get_client(self, agent: A2AAgent) -> httpx.AsyncClient:
        federation_id = agent.federationRefId
        if federation_id is None:
            raise ValueError(f"Azure Foundry A2A agent {agent.path!r} has no federationRefId")

        cached = self._clients.get(federation_id)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            cached = self._clients.get(federation_id)
            if cached is not None:
                return cached

            # Build the credential and the client under the same lock, so invalidate()
            # cannot evict the auth service between the two (atomic with respect to it).
            auth_service = await self._get_auth_service_locked(federation_id)
            client = httpx.AsyncClient(
                auth=AzureEntraAuth(auth_service),
                timeout=httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0),
                limits=self._limits,
            )
            self._clients[federation_id] = client
            return client

    async def invalidate(self, federation_id: PydanticObjectId) -> None:
        """Drop a federation's cached client and credential and close their resources.

        Coordinates through the same per-federation lock ``get_auth_service``/``get_client``
        use, so it always waits for an in-flight build to finish (evicting what was just
        stored, not a stale entry) before clearing the cache.
        """
        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            client = self._clients.pop(federation_id, None)
            if client is not None:
                await client.aclose()
            auth_service = self._auth_services.pop(federation_id, None)
            if auth_service is not None:
                await auth_service.close()

    async def close(self) -> None:
        clients = list(self._clients.values())
        auth_services = list(self._auth_services.values())
        self._clients.clear()
        self._auth_services.clear()
        self._locks.clear()

        for client in clients:
            await client.aclose()
        for auth_service in auth_services:
            await auth_service.close()


__all__: list[str] = ["AzureEntraAuth", "AzureFoundryClientCache"]
