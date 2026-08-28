from __future__ import annotations

import asyncio
import logging
from typing import Any

from azure.core.exceptions import ClientAuthenticationError
from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential

from registry_pkgs.core.crypto_utils import decrypt_value, is_encrypted
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig

logger = logging.getLogger(__name__)


_TOKEN_SCOPE = "https://ai.azure.com/.default"
_PREVIEW_HEADER_NAME = "Foundry-Features"
_PREVIEW_HEADER_VALUE = "HostedAgents=V1Preview"


class AzureFoundryAuthService:
    """Manage Entra credentials for Azure Foundry discovery and invocation."""

    def __init__(self, provider_config: AzureAiFoundryProviderConfig, *, encryption_key: bytes):
        self._config = provider_config
        self._encryption_key = encryption_key
        self._credential: DefaultAzureCredential | ClientSecretCredential | None = None
        self._rebuild_lock = asyncio.Lock()

    @property
    def send_preview_header(self) -> bool:
        return bool(self._config.sendPreviewHeader)

    def credential(self) -> DefaultAzureCredential | ClientSecretCredential:
        if self._credential is None:
            self._credential = self._build_credential()
        return self._credential

    def _build_credential(self) -> DefaultAzureCredential | ClientSecretCredential:
        secret = self._config.clientSecret
        if not secret:
            return DefaultAzureCredential()
        tenant_id = self._config.tenantId
        client_id = self._config.clientId
        if not tenant_id or not client_id:
            raise ValueError(
                "Azure AI Foundry service-principal auth requires tenantId and clientId alongside clientSecret"
            )
        plain_secret = decrypt_value(secret, encryption_key=self._encryption_key) if is_encrypted(secret) else secret
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=plain_secret,
        )

    async def access_token(self) -> str:
        cred = self.credential()
        try:
            return (await cred.get_token(_TOKEN_SCOPE)).token
        except ClientAuthenticationError:
            logger.warning("Azure credential auth failed; rebuilding once and retrying", exc_info=True)
            await self._rebuild_if_stale(cred)
            return (await self.credential().get_token(_TOKEN_SCOPE)).token

    async def _rebuild_if_stale(self, failed_cred: DefaultAzureCredential | ClientSecretCredential) -> None:
        async with self._rebuild_lock:
            # Double-check: another coroutine in the same failure batch may have already
            # replaced the failed credential — if so, don't close the fresh one it built.
            if self._credential is not failed_cred and self._credential is not None:
                return
            if self._credential is not None:
                await self._credential.close()
            self._credential = self._build_credential()

    async def build_headers(self, extra: dict[str, Any] | None = None) -> dict[str, str]:
        token = await self.access_token()
        headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
        if self.send_preview_header:
            headers[_PREVIEW_HEADER_NAME] = _PREVIEW_HEADER_VALUE
        if extra:
            headers.update(extra)
        return headers

    async def close(self) -> None:
        if self._credential is not None:
            await self._credential.close()
            self._credential = None

    async def __aenter__(self) -> AzureFoundryAuthService:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
