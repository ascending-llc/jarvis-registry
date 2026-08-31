from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.core.credentials import AccessToken
from beanie import PydanticObjectId

from registry_pkgs.federation.azure_foundry_auth import AzureFoundryAuthService

_SCOPE = "https://ai.azure.com/.default"


def _cache(token: str = "tok", headers: dict | None = None) -> MagicMock:
    cache = MagicMock()
    cache.get_access_token = AsyncMock(return_value=AccessToken(token, 9_999_999_999))
    cache.build_headers = AsyncMock(
        return_value=headers if headers is not None else {"Authorization": f"Bearer {token}"}
    )
    return cache


@pytest.mark.asyncio
async def test_get_token_delegates_to_cache_with_scope():
    fed = PydanticObjectId()
    cache = _cache("tokX")
    facade = AzureFoundryAuthService(fed, cache)

    token = await facade.get_token(_SCOPE)

    assert token.token == "tokX"
    cache.get_access_token.assert_awaited_once_with(fed, scope=_SCOPE)


@pytest.mark.asyncio
async def test_get_token_defaults_scope_when_omitted():
    fed = PydanticObjectId()
    cache = _cache()
    facade = AzureFoundryAuthService(fed, cache)

    await facade.get_token()

    cache.get_access_token.assert_awaited_once_with(fed, scope=_SCOPE)


@pytest.mark.asyncio
async def test_get_token_ignores_extra_kwargs():
    fed = PydanticObjectId()
    cache = _cache()
    facade = AzureFoundryAuthService(fed, cache)

    # claims/tenant_id/enable_cae are accepted (protocol) but not forwarded.
    await facade.get_token(_SCOPE, claims="x", tenant_id="y", enable_cae=True)

    cache.get_access_token.assert_awaited_once_with(fed, scope=_SCOPE)


@pytest.mark.asyncio
async def test_access_token_returns_token_string():
    fed = PydanticObjectId()
    facade = AzureFoundryAuthService(fed, _cache("abc"))
    assert await facade.access_token() == "abc"


@pytest.mark.asyncio
async def test_build_headers_delegates_to_cache():
    fed = PydanticObjectId()
    cache = _cache(headers={"Authorization": "Bearer h", "Foundry-Features": "HostedAgents=V1Preview"})
    facade = AzureFoundryAuthService(fed, cache)

    headers = await facade.build_headers(extra={"X": "1"})

    assert headers["Authorization"] == "Bearer h"
    assert headers["Foundry-Features"] == "HostedAgents=V1Preview"
    cache.build_headers.assert_awaited_once_with(fed, extra={"X": "1"})
