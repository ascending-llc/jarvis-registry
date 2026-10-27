from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
_TOKEN_EXPIRY_SKEW_SECONDS = 300
_STATE_MAX_AGE_SECONDS = 600
_REBUILD_MIN_INTERVAL_SECONDS = 30
_SAFE_REPLAY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_AUTH_FAILURE_STATUS_CODES = frozenset({401, 403})


class AuthMode(StrEnum):
    CLIENT_SECRET = "client_secret"
    MANAGED_IDENTITY = "managed_identity"


@dataclass
class _CredentialState:
    provider_config: AzureAiFoundryProviderConfig
    mode: AuthMode
    built_at: float = field(default_factory=time.monotonic)
    cached_token: AccessToken | None = None
    long_lived_credential: DefaultAzureCredential | None = None


def _resolve_mode(provider_config: AzureAiFoundryProviderConfig) -> AuthMode:
    return AuthMode.CLIENT_SECRET if provider_config.clientSecret else AuthMode.MANAGED_IDENTITY


def _is_near_expiry(token: AccessToken) -> bool:
    return token.expires_on - time.time() < _TOKEN_EXPIRY_SKEW_SECONDS


class AzureEntraAuth(httpx.Auth):
    """Bounded authentication recovery, only for requests safe to replay."""

    def __init__(self, auth_service: AzureFoundryAuthService) -> None:
        self._auth_service = auth_service

    @staticmethod
    def _apply_headers(request: httpx.Request, headers: dict[str, str]) -> None:
        request.headers.pop(_PREVIEW_HEADER_NAME, None)
        request.headers.update(headers)

    async def async_auth_flow(self, request: httpx.Request) -> AsyncIterator[httpx.Request]:
        headers = await self._auth_service.build_headers()
        self._apply_headers(request, headers)
        response = yield request
        if response.status_code not in _AUTH_FAILURE_STATUS_CODES:
            return
        # Every auth failure heals the cached credential for later requests, but only
        # a 401 on a safe method is replayed: nothing makes arbitrary POSTs or 403s safe to repeat.
        await self._auth_service.rebuild_after_auth_failure()
        if response.status_code != 401 or request.method not in _SAFE_REPLAY_METHODS:
            return
        new_headers = await self._auth_service.build_headers()
        if new_headers == headers:
            return
        await response.aclose()
        self._apply_headers(request, new_headers)
        yield request


class AzureFoundryClientCache:
    """Process-local derived cache; Mongo remains the configuration authority.

    All use of a credential is serialized with its replacement, per federation.
    A mode switch or deletion therefore cannot close a credential during get_token.
    """

    def __init__(
        self, *, encryption_key: bytes, max_connections: int = 300, max_keepalive_connections: int = 20
    ) -> None:
        self._encryption_key = encryption_key
        self._states: dict[PydanticObjectId, _CredentialState] = {}
        self._facades: dict[PydanticObjectId, AzureFoundryAuthService] = {}
        self._clients: dict[PydanticObjectId, httpx.AsyncClient] = {}
        self._locks: dict[PydanticObjectId, asyncio.Lock] = {}
        self._lock_users: dict[PydanticObjectId, int] = {}
        self._last_auth_rebuild: dict[PydanticObjectId, float] = {}
        self._limits = httpx.Limits(
            max_connections=max_connections, max_keepalive_connections=max_keepalive_connections
        )
        self._closed = False

    @asynccontextmanager
    async def _lock(self, federation_id: PydanticObjectId) -> AsyncIterator[None]:
        if self._closed:
            raise RuntimeError("Azure Foundry cache is closed")
        lock = self._locks.setdefault(federation_id, asyncio.Lock())
        self._lock_users[federation_id] = self._lock_users.get(federation_id, 0) + 1
        try:
            async with lock:
                if self._closed:
                    raise RuntimeError("Azure Foundry cache is closed")
                yield
        finally:
            remaining = self._lock_users[federation_id] - 1
            if remaining:
                self._lock_users[federation_id] = remaining
            else:
                self._lock_users.pop(federation_id, None)
                if federation_id not in self._states:
                    self._locks.pop(federation_id, None)
                    self._last_auth_rebuild.pop(federation_id, None)

    async def _rebuild_state_locked(
        self, federation_id: PydanticObjectId, *, auth_failure: bool = False
    ) -> _CredentialState:
        state = self._states.get(federation_id)
        now = time.monotonic()
        previous = self._last_auth_rebuild.get(federation_id)
        if auth_failure:
            if state is not None and previous is not None and now - previous < _REBUILD_MIN_INTERVAL_SECONDS:
                return state
            # Failed attempts count too; never stamp a stale state as fresh.
            self._last_auth_rebuild[federation_id] = now
        federation = await Federation.get(federation_id)
        if federation is None:
            self._states.pop(federation_id, None)
            self._facades.pop(federation_id, None)
            client = self._clients.pop(federation_id, None)
            if client is not None:
                await client.aclose()
            if state is not None and state.long_lived_credential is not None:
                await state.long_lived_credential.close()
            raise ValueError(f"Federation {federation_id} not found")
        if federation.providerType != FederationProviderType.AZURE_AI_FOUNDRY:
            raise ValueError("Federation providerType is not azure_ai_foundry")
        cfg = AzureAiFoundryProviderConfig(**(federation.providerConfig or {}))
        new_state = _CredentialState(provider_config=cfg, mode=_resolve_mode(cfg))
        if state is not None and new_state.mode == state.mode == AuthMode.MANAGED_IDENTITY:
            new_state.long_lived_credential = state.long_lived_credential
        elif state is not None and state.long_lived_credential is not None:
            await state.long_lived_credential.close()
        self._states[federation_id] = new_state
        return new_state

    async def _get_state_locked(self, federation_id: PydanticObjectId) -> _CredentialState:
        state = self._states.get(federation_id)
        if state is None or time.monotonic() - state.built_at >= _STATE_MAX_AGE_SECONDS:
            state = await self._rebuild_state_locked(federation_id)
        return state

    async def force_rebuild(
        self, federation_id: PydanticObjectId, *, auth_failure: bool = False
    ) -> AzureAiFoundryProviderConfig:
        async with self._lock(federation_id):
            return (
                await self._rebuild_state_locked(federation_id, auth_failure=auth_failure)
            ).provider_config.model_copy(deep=True)

    async def get_auth_service(
        self, federation_id: PydanticObjectId, *, expected_config: AzureAiFoundryProviderConfig | None = None
    ) -> AzureFoundryAuthService:
        async with self._lock(federation_id):
            await self._get_state_locked(federation_id)
            if expected_config is not None:
                return AzureFoundryAuthService(federation_id, self, expected_config=expected_config)
            return self._facades.setdefault(federation_id, AzureFoundryAuthService(federation_id, self))

    async def _token_locked(
        self,
        federation_id: PydanticObjectId,
        scope: str,
        expected_config: AzureAiFoundryProviderConfig | None,
        *,
        rebuilt: bool = False,
    ) -> AccessToken:
        state = await self._get_state_locked(federation_id)
        if expected_config is not None and state.provider_config != expected_config:
            raise RuntimeError("Azure configuration changed during discovery; retry synchronization")
        if state.mode == AuthMode.MANAGED_IDENTITY:
            if state.long_lived_credential is None:
                state.long_lived_credential = DefaultAzureCredential()
            return await state.long_lived_credential.get_token(scope)
        if state.cached_token is not None and not _is_near_expiry(state.cached_token):
            return state.cached_token
        try:
            token = await self._fetch_client_secret_token(state.provider_config, scope)
        except ClientAuthenticationError:
            if rebuilt:
                raise
            updated = await self._rebuild_state_locked(federation_id, auth_failure=True)
            if updated is state:
                raise
            return await self._token_locked(federation_id, scope, expected_config, rebuilt=True)
        state.cached_token = token
        return token

    async def get_access_token(
        self,
        federation_id: PydanticObjectId,
        *,
        scope: str = _TOKEN_SCOPE,
        expected_config: AzureAiFoundryProviderConfig | None = None,
    ) -> AccessToken:
        async with self._lock(federation_id):
            return await self._token_locked(federation_id, scope, expected_config)

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

    async def build_headers(
        self,
        federation_id: PydanticObjectId,
        extra: dict[str, Any] | None = None,
        *,
        expected_config: AzureAiFoundryProviderConfig | None = None,
    ) -> dict[str, str]:
        async with self._lock(federation_id):
            token = await self._token_locked(federation_id, _TOKEN_SCOPE, expected_config)
            headers = dict(extra or {})
            headers["Authorization"] = f"Bearer {token.token}"
            headers.pop(_PREVIEW_HEADER_NAME, None)
            if self._states[federation_id].provider_config.sendPreviewHeader:
                headers[_PREVIEW_HEADER_NAME] = _PREVIEW_HEADER_VALUE
            return headers

    async def get_client(self, agent: A2AAgent) -> httpx.AsyncClient:
        federation_id = agent.federationRefId
        if federation_id is None:
            raise ValueError(f"Azure Foundry A2A agent {agent.path!r} has no federationRefId")
        async with self._lock(federation_id):
            await self._get_state_locked(federation_id)
            if federation_id not in self._clients:
                facade = self._facades.setdefault(federation_id, AzureFoundryAuthService(federation_id, self))
                self._clients[federation_id] = httpx.AsyncClient(
                    auth=AzureEntraAuth(facade),
                    timeout=httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0),
                    limits=self._limits,
                )
            return self._clients[federation_id]

    async def close(self) -> None:
        self._closed = True
        for federation_id, lock in list(self._locks.items()):
            async with lock:
                client = self._clients.pop(federation_id, None)
                if client is not None:
                    await client.aclose()
                state = self._states.pop(federation_id, None)
                if state is not None and state.long_lived_credential is not None:
                    await state.long_lived_credential.close()
        self._facades.clear()
        self._locks.clear()
        self._last_auth_rebuild.clear()


__all__ = ["AzureEntraAuth", "AzureFoundryClientCache"]
