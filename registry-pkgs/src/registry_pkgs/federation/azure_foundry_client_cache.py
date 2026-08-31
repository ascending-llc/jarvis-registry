from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential
from beanie import PydanticObjectId

from registry_pkgs.core.crypto_utils import decrypt_value, is_encrypted
from registry_pkgs.models import A2AAgent
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig, Federation

from .azure_foundry_auth import AzureFoundryAuthService

logger = logging.getLogger(__name__)

_TOKEN_SCOPE = "https://ai.azure.com/.default"
_PREVIEW_HEADER_NAME = "Foundry-Features"
_PREVIEW_HEADER_VALUE = "HostedAgents=V1Preview"
_TOKEN_EXPIRY_SKEW_SECONDS = 300  # refresh this many seconds before actual expiry


class AuthMode(StrEnum):
    CLIENT_SECRET = "client_secret"
    MANAGED_IDENTITY = "managed_identity"


@dataclass
class _CredentialState:
    """Per-federation credential state owned by the cache."""

    provider_config: AzureAiFoundryProviderConfig
    config_updated_at: datetime
    mode: AuthMode
    cached_token: AccessToken | None = None  # client_secret mode only
    long_lived_credential: DefaultAzureCredential | None = None  # managed_identity mode only


def _resolve_mode(provider_config: AzureAiFoundryProviderConfig) -> AuthMode:
    return AuthMode.CLIENT_SECRET if provider_config.clientSecret else AuthMode.MANAGED_IDENTITY


def _is_near_expiry(token: AccessToken) -> bool:
    return token.expires_on - time.time() < _TOKEN_EXPIRY_SKEW_SECONDS


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
    """Single, shared owner of all Azure Foundry credential/token state, per federation."""

    def __init__(
        self,
        *,
        encryption_key: bytes,
        max_connections: int = 300,
        max_keepalive_connections: int = 20,
    ):
        self._encryption_key = encryption_key
        self._states: dict[PydanticObjectId, _CredentialState] = {}
        self._facades: dict[PydanticObjectId, AzureFoundryAuthService] = {}
        self._clients: dict[PydanticObjectId, httpx.AsyncClient] = {}
        self._locks: dict[PydanticObjectId, asyncio.Lock] = {}
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )

    async def _get_state_locked(self, federation_id: PydanticObjectId) -> _CredentialState:
        cached = self._states.get(federation_id)
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
        state = _CredentialState(provider_config=cfg, config_updated_at=federation.updatedAt, mode=_resolve_mode(cfg))
        self._states[federation_id] = state
        return state

    async def _ensure_state(self, federation_id: PydanticObjectId) -> _CredentialState:
        """Return the federation's state, building it under the lock if missing."""
        state = self._states.get(federation_id)
        if state is not None:
            return state
        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            return await self._get_state_locked(federation_id)

    async def _get_facade_locked(self, federation_id: PydanticObjectId) -> AzureFoundryAuthService:
        await self._get_state_locked(federation_id)
        return self._facades.setdefault(federation_id, AzureFoundryAuthService(federation_id, self))

    async def get_auth_service(self, federation_id: PydanticObjectId) -> AzureFoundryAuthService:
        """Return the stable per-federation facade, building state on first use (one per federation).

        This is the single entry point every external consumer uses to reach Azure Foundry:

        - workflow A2A node invocation (``A2aHeadersProvider``, registry-triggered and worker-scheduled)
          → ``facade.build_headers()``.
        - federation sync discovery (``AzureAiFoundrySyncHandler``) → the facade is passed to
          ``AIProjectClient`` as an ``AsyncTokenCredential`` (``get_token``), plus ``build_headers()``
          to enrich each discovered agent card.
        - agent-card re-discovery (``A2AAgentService.sync_wellknown`` / ``refresh_agent_capabilities``)
          → ``facade.build_headers()``.

        The direct-proxy client path (``get_client``) builds the *same* facade via the shared
        ``_get_facade_locked`` core rather than calling this method: it already holds the federation
        lock, and this method acquires it (the lock is not re-entrant). All four paths ultimately
        funnel into ``get_access_token``, so token caching / rotation self-heal live in one place.
        """
        cached = self._facades.get(federation_id)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            return await self._get_facade_locked(federation_id)

    async def get_access_token(self, federation_id: PydanticObjectId, *, scope: str = _TOKEN_SCOPE) -> AccessToken:
        state = self._states.get(federation_id)
        if state is None:
            state = await self._ensure_state(federation_id)

        if state.mode == AuthMode.MANAGED_IDENTITY:
            credential = await self._get_managed_identity_credential(federation_id, state)
            return await credential.get_token(scope)

        # Fast path: reuse a still-valid cached token without taking the lock.
        if state.cached_token is not None and not _is_near_expiry(state.cached_token):
            return state.cached_token

        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            return await self._get_client_secret_token_locked(federation_id, scope)

    async def _get_client_secret_token_locked(
        self, federation_id: PydanticObjectId, scope: str, *, rebuilt: bool = False
    ) -> AccessToken:
        """Caller MUST hold this federation's lock.

        Re-reads state and re-dispatches on ``mode`` on every entry — including after a reactive
        rebuild — because ``_refresh_state_if_config_changed`` may have flipped the federation from
        client_secret to managed_identity (a config edit that cleared ``clientSecret``). ``rebuilt``
        bounds the reactive rebuild to exactly one attempt (no unbounded recursion if ``updatedAt``
        keeps advancing).
        """
        state = self._states[federation_id]
        if state.mode == AuthMode.MANAGED_IDENTITY:
            credential = self._ensure_managed_credential_locked(state)
            return await credential.get_token(scope)

        if state.cached_token is not None and not _is_near_expiry(state.cached_token):
            return state.cached_token

        try:
            token = await self._fetch_client_secret_token(state.provider_config, scope)
        except ClientAuthenticationError:
            if rebuilt or not await self._refresh_state_if_config_changed(federation_id, state):
                raise
            return await self._get_client_secret_token_locked(federation_id, scope, rebuilt=True)

        state.cached_token = token
        return token

    async def _fetch_client_secret_token(
        self, provider_config: AzureAiFoundryProviderConfig, scope: str
    ) -> AccessToken:
        tenant_id = provider_config.tenantId
        client_id = provider_config.clientId
        if not tenant_id or not client_id:
            raise ValueError(
                "Azure AI Foundry service-principal auth requires tenantId and clientId alongside clientSecret"
            )
        secret = provider_config.clientSecret or ""
        plain_secret = decrypt_value(secret, encryption_key=self._encryption_key) if is_encrypted(secret) else secret
        # Disposable per fetch: constructed cheaply (no network), closed deterministically by the
        # async-with before returning — nothing long-lived or shared exists for client_secret mode.
        async with ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=plain_secret,
        ) as cred:
            return await cred.get_token(scope)

    def _ensure_managed_credential_locked(self, state: _CredentialState) -> DefaultAzureCredential:
        """Build the long-lived DefaultAzureCredential on first use. Caller MUST hold the fed lock."""
        if state.long_lived_credential is None:
            state.long_lived_credential = DefaultAzureCredential()
        return state.long_lived_credential

    async def _get_managed_identity_credential(
        self, federation_id: PydanticObjectId, state: _CredentialState
    ) -> DefaultAzureCredential:
        if state.long_lived_credential is not None:
            return state.long_lived_credential
        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            return self._ensure_managed_credential_locked(state)

    async def _refresh_state_if_config_changed(self, federation_id: PydanticObjectId, state: _CredentialState) -> bool:
        """Re-read Mongo; rebuild state from the new config only if ``updatedAt`` advanced."""
        federation = await Federation.get(federation_id)
        if federation is None or federation.updatedAt <= state.config_updated_at:
            return False
        cfg = AzureAiFoundryProviderConfig(**(federation.providerConfig or {}))
        self._states[federation_id] = _CredentialState(
            provider_config=cfg, config_updated_at=federation.updatedAt, mode=_resolve_mode(cfg)
        )
        return True

    async def build_headers(
        self, federation_id: PydanticObjectId, extra: dict[str, Any] | None = None
    ) -> dict[str, str]:
        token = await self.get_access_token(federation_id)
        headers: dict[str, str] = {"Authorization": f"Bearer {token.token}"}
        state = self._states[federation_id]
        if state.provider_config.sendPreviewHeader:
            headers[_PREVIEW_HEADER_NAME] = _PREVIEW_HEADER_VALUE
        if extra:
            headers.update(extra)
        return headers

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

            # Build state/facade and the client under one lock, atomic w.r.t. invalidate().
            facade = await self._get_facade_locked(federation_id)
            client = httpx.AsyncClient(
                auth=AzureEntraAuth(facade),
                timeout=httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0),
                limits=self._limits,
            )
            self._clients[federation_id] = client
            return client

    async def invalidate(self, federation_id: PydanticObjectId) -> None:
        """Drop a federation's cached client; refresh credential state, mode-conditionally.

        A `managed_identity`-mode federation that stays in that mode keeps its long-lived
        `DefaultAzureCredential` untouched (nothing federation-derived to refresh, and rebuilding
        it repeats expensive provider-chain discovery for no benefit) — otherwise a concurrent
        caller mid-`get_token()` on the shared credential could hit a use-after-close failure when
        this call tears it down. `client_secret`-mode federations, and any federation switching
        mode, get a fresh state (dropping any cached token / closing any managed credential).
        """
        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        async with lock:
            client = self._clients.pop(federation_id, None)
            if client is not None:
                await client.aclose()

            state = self._states.get(federation_id)
            if state is None:
                return

            federation = await Federation.get(federation_id)
            if federation is None:
                self._states.pop(federation_id, None)
                if state.long_lived_credential is not None:
                    await state.long_lived_credential.close()
                return

            cfg = AzureAiFoundryProviderConfig(**(federation.providerConfig or {}))
            new_mode = _resolve_mode(cfg)

            if new_mode == AuthMode.MANAGED_IDENTITY and state.mode == AuthMode.MANAGED_IDENTITY:
                state.provider_config = cfg
                state.config_updated_at = federation.updatedAt
                return

            if state.long_lived_credential is not None:
                await state.long_lived_credential.close()
            self._states[federation_id] = _CredentialState(
                provider_config=cfg, config_updated_at=federation.updatedAt, mode=new_mode
            )

    async def close(self) -> None:
        clients = list(self._clients.values())
        states = list(self._states.values())
        self._clients.clear()
        self._states.clear()
        self._facades.clear()
        self._locks.clear()

        for client in clients:
            await client.aclose()
        for state in states:
            if state.long_lived_credential is not None:
                await state.long_lived_credential.close()


__all__: list[str] = ["AzureEntraAuth", "AzureFoundryClientCache"]
