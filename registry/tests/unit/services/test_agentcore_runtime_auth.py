import pytest

from registry.core.config import settings
from registry.services.federation.agentcore_clients import AgentCoreClientProvider
from registry.services.federation.agentcore_runtime_auth import AgentCoreRuntimeAuthService
from registry_pkgs.core.jwt_utils import decode_jwt_unverified
from registry_pkgs.models.federation import AgentCoreRuntimeAccessConfig


class _FakeProvider(AgentCoreClientProvider):
    async def get_runtime_client(self, _region: str, assume_role_arn: str | None = None):
        return object()

    async def get_runtime_credentials_provider(self, _region: str, assume_role_arn: str | None = None):
        return lambda: None


def _make_runtime_access(mode: str, jwt: dict | None = None):
    payload = {"mode": mode, "iam": {}}
    if jwt is not None:
        payload["jwt"] = jwt
    return AgentCoreRuntimeAccessConfig(**payload)


@pytest.mark.asyncio
async def test_build_runtime_http_auth_iam_returns_sigv4_when_modes_match():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda arn: arn.split(":")[3],
    )
    runtime_access = _make_runtime_access("iam")

    headers, auth = await service.build_runtime_http_auth(
        runtime_access=runtime_access,
        metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/demo"},
        runtime_detail={},
        region="us-east-1",
        assume_role_arn=None,
    )

    assert headers == {}
    assert auth is not None


@pytest.mark.asyncio
async def test_build_runtime_http_auth_trusts_explicit_runtime_access_over_detection():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda arn: arn.split(":")[3],
    )
    runtime_access = _make_runtime_access("iam")

    headers, auth = await service.build_runtime_http_auth(
        runtime_access=runtime_access,
        metadata={
            "runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/demo",
            "authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}},
        },
        runtime_detail=None,
        region="us-east-1",
        assume_role_arn=None,
    )

    assert headers == {}
    assert auth is not None


def test_detect_agentcore_data_plane_auth_mode_treats_empty_jwt_shell_as_iam():
    assert (
        AgentCoreRuntimeAuthService.detect_agentcore_data_plane_auth_mode(
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {}}},
            runtime_detail=None,
        )
        == "IAM"
    )


def test_detect_agentcore_data_plane_auth_mode_treats_oauth_authorizer_as_jwt():
    assert (
        AgentCoreRuntimeAuthService.detect_agentcore_data_plane_auth_mode(
            metadata={"authorizerConfiguration": {"authorizerType": "OAUTH"}},
            runtime_detail=None,
        )
        == "JWT"
    )


@pytest.mark.asyncio
async def test_build_runtime_http_auth_jwt_signs_bearer_token_with_expected_claims():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda arn: arn.split(":")[3],
    )
    runtime_access = _make_runtime_access(
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
        runtime_access=runtime_access,
        metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
        runtime_detail=None,
        region="us-east-1",
        assume_role_arn=None,
    )

    assert auth is None
    token = headers["Authorization"].split(" ", 1)[1]
    claims = decode_jwt_unverified(token)
    # iss is derived from discoveryUrl (scheme + host), not from settings.jwt_issuer
    assert claims["iss"] == "https://issuer"
    assert claims["sub"] == settings.registry_app_name
    assert claims["aud"] == settings.jwt_audience
    assert claims["client_id"] == "jarvis-registry"
    assert claims["scope"] == "sync:read tools:read"
    assert claims["tenant"] == "prod"


@pytest.mark.asyncio
async def test_build_runtime_http_auth_jwt_uses_global_audience():
    service = AgentCoreRuntimeAuthService(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda arn: arn.split(":")[3],
    )
    runtime_access = _make_runtime_access(
        "jwt", jwt={"discoveryUrl": "https://issuer/.well-known/openid-configuration"}
    )

    headers, _ = await service.build_runtime_http_auth(
        runtime_access=runtime_access,
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
        extract_region_from_arn=lambda arn: arn.split(":")[3],
    )

    with pytest.raises(ValueError, match="resource-level runtimeAccess.jwt configuration"):
        await service.build_runtime_http_auth(
            runtime_access={"mode": "jwt", "iam": {}},
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            runtime_detail=None,
            region="us-east-1",
            assume_role_arn=None,
        )
