from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.federation.azure_foundry_proxy_auth import A2aHeadersProvider
from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.enums import FederationProviderType


def _jwt_config() -> JwtSigningConfig:
    return JwtSigningConfig(
        jwt_private_key="fake-pem",
        jwt_issuer="https://jarvis.example.com",
        jwt_audience="https://jarvis.example.com",
        jwt_self_signed_kid="kid",
    )


def _agent(*, provider: str | None, federation_id: PydanticObjectId | None) -> SimpleNamespace:
    """Lightweight stand-in for A2AAgent — providers only read these two fields."""
    meta = {} if provider is None else {"providerType": provider}
    return SimpleNamespace(
        federationMetadata=meta,
        federationRefId=federation_id,
        path="/test-agent",
        card=SimpleNamespace(url="https://agent.example.com"),
        config=None,
    )


def _federation(provider_type: str, provider_config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        providerType=provider_type,
        providerConfig=provider_config
        or {
            "projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p",
            "tenantId": "tenant",
            "clientId": "client",
            "clientSecret": "plain-secret",
        },
    )


def _auth_cm_mock(headers: dict) -> MagicMock:
    """Mock AzureFoundryAuthService usable as an async context manager."""
    auth = MagicMock()
    auth.build_headers = AsyncMock(return_value=headers)
    auth.__aenter__ = AsyncMock(return_value=auth)
    auth.__aexit__ = AsyncMock(return_value=False)
    return auth


@pytest.mark.asyncio
async def test_non_azure_agent_falls_back_to_self_signed_jwt():
    """AWS / plain-JWT agents must continue to use the existing build_headers path —
    Federation must not be touched."""
    provider = A2aHeadersProvider(jwt_config=_jwt_config())
    agent = _agent(provider="aws_agentcore", federation_id=PydanticObjectId())

    with (
        patch(
            "registry.services.federation.azure_foundry_proxy_auth.build_headers",
            return_value={"Authorization": "Bearer self-signed"},
        ) as build_headers_spy,
        patch(
            "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
            new=AsyncMock(),
        ) as federation_get,
    ):
        headers = await provider(agent)

    assert headers == {"Authorization": "Bearer self-signed"}
    build_headers_spy.assert_called_once()
    federation_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_azure_agent_uses_federation_and_returns_entra_bearer():
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id

    fake_auth = _auth_cm_mock({"Authorization": "Bearer entra-token"})

    provider = A2aHeadersProvider(jwt_config=_jwt_config())
    agent = _agent(provider="azure_ai_foundry", federation_id=federation_id)

    with (
        patch(
            "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
            new=AsyncMock(return_value=federation),
        ),
        patch(
            "registry.services.federation.azure_foundry_proxy_auth.AzureFoundryAuthService",
            return_value=fake_auth,
        ),
    ):
        headers = await provider(agent)

    assert headers == {"Authorization": "Bearer entra-token"}
    fake_auth.build_headers.assert_awaited_once()


@pytest.mark.asyncio
async def test_azure_credential_resolved_fresh_each_call():
    """No caching: each call re-reads the Federation and builds a fresh auth
    service, so a rotated providerConfig takes effect immediately."""
    federation_id = PydanticObjectId()
    federation = _federation(FederationProviderType.AZURE_AI_FOUNDRY.value)
    federation.id = federation_id

    fake_auth = _auth_cm_mock({"Authorization": "Bearer entra-token"})

    provider = A2aHeadersProvider(jwt_config=_jwt_config())
    agent = _agent(provider="azure_ai_foundry", federation_id=federation_id)

    federation_get = AsyncMock(return_value=federation)
    auth_factory = MagicMock(return_value=fake_auth)

    with (
        patch("registry.services.federation.azure_foundry_proxy_auth.Federation.get", new=federation_get),
        patch(
            "registry.services.federation.azure_foundry_proxy_auth.AzureFoundryAuthService",
            new=auth_factory,
        ),
    ):
        await provider(agent)
        await provider(agent)

    assert federation_get.await_count == 2
    assert auth_factory.call_count == 2
    assert fake_auth.build_headers.await_count == 2


@pytest.mark.asyncio
async def test_azure_agent_without_federation_ref_raises():
    provider = A2aHeadersProvider(jwt_config=_jwt_config())
    agent = _agent(provider="azure_ai_foundry", federation_id=None)

    with pytest.raises(ValueError, match="federationRefId"):
        await provider(agent)


@pytest.mark.asyncio
async def test_azure_provider_with_missing_federation_raises():
    provider = A2aHeadersProvider(jwt_config=_jwt_config())
    agent = _agent(provider="azure_ai_foundry", federation_id=PydanticObjectId())

    with patch(
        "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="not found"):
            await provider(agent)


@pytest.mark.asyncio
async def test_azure_provider_rejects_wrong_provider_type_federation():
    federation_id = PydanticObjectId()
    federation = _federation("aws_agentcore", provider_config={})
    federation.id = federation_id

    provider = A2aHeadersProvider(jwt_config=_jwt_config())
    agent = _agent(provider="azure_ai_foundry", federation_id=federation_id)

    with patch(
        "registry.services.federation.azure_foundry_proxy_auth.Federation.get",
        new=AsyncMock(return_value=federation),
    ):
        with pytest.raises(ValueError, match="is not azure_ai_foundry"):
            await provider(agent)
