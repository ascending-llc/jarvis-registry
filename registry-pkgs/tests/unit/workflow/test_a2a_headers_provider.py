from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry_pkgs.workflows.a2a_headers_provider import A2aHeadersProvider, make_a2a_headers_provider

_MOD = "registry_pkgs.workflows.a2a_headers_provider"


def _provider(cache: MagicMock) -> A2aHeadersProvider:
    return make_a2a_headers_provider(jwt_config=object(), azure_client_cache=cache)


@pytest.mark.asyncio
async def test_non_azure_agent_uses_jwt_build_headers():
    cache = MagicMock()
    cache.get_auth_service = AsyncMock()
    provider = _provider(cache)
    agent = SimpleNamespace(path="managed-agent", federationRefId=None)
    jwt_headers = {"Authorization": "Bearer jwt"}

    with (
        patch(f"{_MOD}.is_azure_foundry_runtime", return_value=False),
        patch(f"{_MOD}.build_headers", return_value=jwt_headers) as build,
    ):
        result = await provider(agent)

    assert result == jwt_headers
    build.assert_called_once_with(agent, jwt_config=provider._jwt_config)
    cache.get_auth_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_azure_agent_resolves_headers_through_shared_cache():
    federation_id = PydanticObjectId()
    headers = {"Authorization": "Bearer entra"}
    auth = SimpleNamespace(build_headers=AsyncMock(return_value=headers))
    cache = MagicMock()
    cache.get_auth_service = AsyncMock(return_value=auth)
    provider = _provider(cache)
    agent = SimpleNamespace(path="foundry-agent", federationRefId=federation_id)

    with patch(f"{_MOD}.is_azure_foundry_runtime", return_value=True):
        result = await provider(agent)

    assert result == headers
    cache.get_auth_service.assert_awaited_once_with(federation_id)
    auth.build_headers.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_azure_agent_without_federation_ref_raises():
    cache = MagicMock()
    cache.get_auth_service = AsyncMock()
    provider = _provider(cache)
    agent = SimpleNamespace(path="foundry-agent", federationRefId=None)

    with patch(f"{_MOD}.is_azure_foundry_runtime", return_value=True):
        with pytest.raises(ValueError, match="has no federationRefId"):
            await provider(agent)
