from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.auth.oauth.oauth_client import OAuthClient
from registry.services.federation.agentcore_clients import AgentCoreClientProvider
from registry.services.federation.agentcore_runtime_auth import AgentCoreRuntimeAuthService
from registry.services.oauth.token_service import TokenService


class _FakeProvider(AgentCoreClientProvider):
    async def get_runtime_client(self, _region: str, assume_role_arn: str | None = None):
        return object()

    async def get_runtime_credentials_provider(self, _region: str, assume_role_arn: str | None = None):
        return lambda: None


def _make_federation(mode: str, jwt: dict | None = None):
    return SimpleNamespace(
        id=PydanticObjectId(),
        providerConfig={
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole",
            "resourceTagsFilter": {},
            "runtimeAccess": {"mode": mode, "iam": {}, "jwt": jwt},
        },
    )


@pytest.mark.asyncio
async def test_build_runtime_http_auth_iam_returns_sigv4_when_modes_match():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
    )
    federation = _make_federation("iam")

    headers, auth = await service.build_runtime_http_auth(
        federation=federation,
        metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/demo"},
        runtime_detail={},
        region="us-east-1",
        assume_role_arn=None,
    )

    assert headers == {}
    assert auth is not None


@pytest.mark.asyncio
async def test_build_runtime_http_auth_rejects_mode_mismatch():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
    )
    federation = _make_federation("iam")

    with pytest.raises(ValueError, match="runtimeAccess.mode=iam"):
        await service.build_runtime_http_auth(
            federation=federation,
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            runtime_detail=None,
            region="us-east-1",
            assume_role_arn=None,
        )


@pytest.mark.asyncio
async def test_build_runtime_http_auth_jwt_uses_federation_override_and_cache(monkeypatch):
    token_service = AsyncMock(spec=TokenService)
    token_service.get_federation_secret = AsyncMock(return_value="secret-123")
    oauth_client = AsyncMock(spec=OAuthClient)
    oauth_client.fetch_client_credentials_token = AsyncMock(
        return_value=SimpleNamespace(access_token="tok-123", expires_at=9999999999)
    )

    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
        token_service=token_service,
        oauth_client=oauth_client,
    )
    monkeypatch.setattr(service, "_discover_token_endpoint", AsyncMock(return_value="https://issuer/token"))
    federation = _make_federation(
        "jwt",
        jwt={
            "clientId": "client-1",
            "clientSecretRef": "ref-1",
            "discoveryUrl": "https://issuer/.well-known/openid-configuration",
            "audience": "jarvis-services",
            "scope": "sync:read",
        },
    )

    headers_1, auth_1 = await service.build_runtime_http_auth(
        federation=federation,
        metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
        runtime_detail=None,
        region="us-east-1",
        assume_role_arn=None,
    )
    headers_2, auth_2 = await service.build_runtime_http_auth(
        federation=federation,
        metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
        runtime_detail=None,
        region="us-east-1",
        assume_role_arn=None,
    )

    assert headers_1["Authorization"] == "Bearer tok-123"
    assert auth_1 is None
    assert headers_2["Authorization"] == "Bearer tok-123"
    assert auth_2 is None
    oauth_client.fetch_client_credentials_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_runtime_http_auth_jwt_requires_secret():
    token_service = AsyncMock(spec=TokenService)
    token_service.get_federation_secret = AsyncMock(return_value=None)
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
        token_service=token_service,
        oauth_client=AsyncMock(spec=OAuthClient),
    )
    federation = _make_federation("jwt", jwt={"clientId": "client-1", "clientSecretRef": "ref-1"})

    with pytest.raises(ValueError, match="client secret is missing"):
        await service.build_runtime_http_auth(
            federation=federation,
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            runtime_detail=None,
            region="us-east-1",
            assume_role_arn=None,
        )


@pytest.mark.asyncio
async def test_build_runtime_http_auth_jwt_without_federation_context_fails():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
    )

    with pytest.raises(ValueError, match="requires federation context"):
        await service.build_runtime_http_auth(
            federation=None,
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            runtime_detail=None,
            region="us-east-1",
            assume_role_arn=None,
        )
