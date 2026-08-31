from __future__ import annotations

from typing import TYPE_CHECKING, Any

from azure.core.credentials import AccessToken
from beanie import PydanticObjectId

if TYPE_CHECKING:
    from .azure_foundry_client_cache import AzureFoundryClientCache

_TOKEN_SCOPE = "https://ai.azure.com/.default"


class AzureFoundryAuthService:
    """Stable, per-federation facade over ``AzureFoundryClientCache``."""

    def __init__(self, federation_id: PydanticObjectId, cache: AzureFoundryClientCache) -> None:
        self._federation_id = federation_id
        self._cache = cache

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        # Only the scope is honored. claims / tenant_id / enable_cae kwargs are intentionally
        # ignored — the cache stores one token per federation for _TOKEN_SCOPE. Revisit if a
        # CAE / claims-challenge or multi-scope flow is ever required (see spec "Out of scope").
        scope = scopes[0] if scopes else _TOKEN_SCOPE
        return await self._cache.get_access_token(self._federation_id, scope=scope)

    async def access_token(self) -> str:
        token = await self.get_token(_TOKEN_SCOPE)
        return token.token

    async def build_headers(self, extra: dict[str, Any] | None = None) -> dict[str, str]:
        return await self._cache.build_headers(self._federation_id, extra=extra)
