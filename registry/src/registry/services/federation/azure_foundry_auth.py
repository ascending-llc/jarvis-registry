from __future__ import annotations

import logging
from typing import Any

from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential

from registry.utils.crypto_utils import decrypt_value, is_encrypted
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig

logger = logging.getLogger(__name__)


_TOKEN_SCOPE = "https://ai.azure.com/.default"
_PREVIEW_HEADER_NAME = "Foundry-Features"
_PREVIEW_HEADER_VALUE = "HostedAgents=V1Preview"


class AzureFoundryAuthService:
    """Owns the Entra credential lifecycle for one discovery/invocation run.

    Two authentication modes, selected implicitly by `provider_config.clientSecret`:
    """

    def __init__(self, provider_config: AzureAiFoundryProviderConfig):
        self._config = provider_config
        self._credential: DefaultAzureCredential | ClientSecretCredential | None = None

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
        plain_secret = decrypt_value(secret) if is_encrypted(secret) else secret
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=plain_secret,
        )

    async def access_token(self) -> str:
        token = await self.credential().get_token(_TOKEN_SCOPE)
        return token.token

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
