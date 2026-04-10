from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId

from registry.core.config import settings
from registry.services.federation.agentcore_clients import AgentCoreClientProvider
from registry.services.federation.agentcore_runtime_auth import AgentCoreRuntimeAuthService
from registry_pkgs.core.jwt_utils import decode_jwt_unverified


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
async def test_build_runtime_http_auth_jwt_signs_bearer_token_with_expected_claims():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
    )
    federation = _make_federation(
        "jwt",
        jwt={
            "discoveryUrl": "https://issuer/.well-known/openid-configuration",
            "audiences": ["jarvis-services", "agentcore-runtime"],
            "allowedClients": ["jarvis-registry"],
            "allowedScopes": ["sync:read", "tools:read"],
            "customClaims": {"tenant": "prod"},
        },
    )

    headers, auth = await service.build_runtime_http_auth(
        federation=federation,
        metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
        runtime_detail=None,
        region="us-east-1",
        assume_role_arn=None,
    )

    assert auth is None
    token = headers["Authorization"].split(" ", 1)[1]
    claims = decode_jwt_unverified(token)
    assert claims["iss"] == settings.jwt_issuer
    assert claims["sub"] == settings.registry_app_name
    assert claims["aud"] == ["jarvis-services", "agentcore-runtime"]
    assert claims["client_id"] == "jarvis-registry"
    assert claims["scope"] == "sync:read tools:read"
    assert claims["tenant"] == "prod"


@pytest.mark.asyncio
async def test_build_runtime_http_auth_jwt_uses_global_audience_fallback():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
    )
    federation = _make_federation("jwt", jwt={"discoveryUrl": "https://issuer/.well-known/openid-configuration"})

    headers, _ = await service.build_runtime_http_auth(
        federation=federation,
        metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
        runtime_detail=None,
        region="us-east-1",
        assume_role_arn=None,
    )

    token = headers["Authorization"].split(" ", 1)[1]
    claims = decode_jwt_unverified(token)
    assert claims["aud"] == settings.jwt_audience


@pytest.mark.asyncio
async def test_build_runtime_http_auth_jwt_requires_federation_context():
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
