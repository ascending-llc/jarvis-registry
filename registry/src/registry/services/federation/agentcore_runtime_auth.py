from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from registry.auth.oauth.oauth_client import OAuthClient
from registry.core.config import settings
from registry.services.oauth.token_service import TokenService
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.models.federation import AwsAgentCoreProviderConfig, Federation

from .agentcore_clients import AgentCoreClientProvider

logger = logging.getLogger(__name__)


class _SigV4HttpxAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, service: str, region: str, credentials_provider: Callable[[], Any]):
        self.service = service
        self.region = region
        self.credentials_provider = credentials_provider

    def auth_flow(self, request: httpx.Request):
        credentials = self.credentials_provider()
        credentials = credentials.get_frozen_credentials()

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(credentials, self.service, self.region).add_auth(aws_request)

        for key, value in aws_request.headers.items():
            request.headers[key] = value
        yield request


@dataclass
class _CachedOAuthToken:
    access_token: str
    expires_at: int | None

    def is_valid(self) -> bool:
        if self.expires_at is None:
            return True
        return self.expires_at > int(time.time()) + 5


class AgentCoreRuntimeAuthService:
    def __init__(
        self,
        *,
        client_provider: AgentCoreClientProvider,
        extract_region_from_arn: Callable[[str, str], str],
        token_service: TokenService | None = None,
        oauth_client: OAuthClient | None = None,
    ):
        self.client_provider = client_provider
        self.extract_region_from_arn = extract_region_from_arn
        self.token_service = token_service
        self.oauth_client = oauth_client or OAuthClient()
        self._jwt_cache: dict[str, _CachedOAuthToken] = {}

    async def build_runtime_http_auth(
        self,
        *,
        federation: Federation | None,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        discovered_mode = self.detect_agentcore_data_plane_auth_mode(metadata=metadata, runtime_detail=runtime_detail)
        if federation is None:
            if discovered_mode == "JWT":
                raise ValueError("JWT runtime auth requires federation context")
            return await self._build_iam_auth(
                metadata=metadata,
                region=region,
                assume_role_arn=assume_role_arn,
            )

        configured_mode = self._configured_mode(federation)

        if configured_mode == AgentCoreRuntimeAccessMode.IAM and discovered_mode != "IAM":
            raise ValueError("Federation runtimeAccess.mode=iam but discovered runtime auth mode is JWT")
        if configured_mode == AgentCoreRuntimeAccessMode.JWT and discovered_mode != "JWT":
            raise ValueError("Federation runtimeAccess.mode=jwt but discovered runtime auth mode is IAM")

        if configured_mode == AgentCoreRuntimeAccessMode.JWT:
            return await self._build_jwt_auth(
                federation=federation,
                metadata=metadata,
                runtime_detail=runtime_detail,
            )
        return await self._build_iam_auth(
            metadata=metadata,
            region=region,
            assume_role_arn=assume_role_arn,
        )

    @staticmethod
    def detect_agentcore_data_plane_auth_mode(
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None = None,
    ) -> str:
        config = (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration") or {}
        text = str(config).upper()
        if "JWT" in text:
            return "JWT"
        return "IAM"

    @staticmethod
    def _configured_mode(federation: Federation | None) -> AgentCoreRuntimeAccessMode:
        if federation is None:
            return AgentCoreRuntimeAccessMode.IAM
        provider_config = AwsAgentCoreProviderConfig(**dict(federation.providerConfig or {}))
        return provider_config.runtimeAccess.mode

    async def _build_iam_auth(
        self,
        *,
        metadata: dict[str, Any],
        region: str,
        assume_role_arn: str | None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        resolved_region = self.extract_region_from_arn(metadata.get("runtimeArn", ""), region)
        credentials_provider = await self.client_provider.get_runtime_credentials_provider(
            resolved_region,
            assume_role_arn,
        )
        if not credentials_provider:
            raise ValueError(f"Failed to initialize runtime credentials provider for region {resolved_region}")
        return {}, _SigV4HttpxAuth("bedrock-agentcore", resolved_region, credentials_provider)

    async def _build_jwt_auth(
        self,
        *,
        federation: Federation | None,
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        if federation is None:
            raise ValueError("JWT runtime auth requires federation context")
        provider_config = AwsAgentCoreProviderConfig(**dict(federation.providerConfig or {}))
        jwt_config = provider_config.runtimeAccess.jwt
        if jwt_config is None or not jwt_config.clientId or not jwt_config.clientSecretRef:
            raise ValueError("JWT runtime requires runtimeAccess.jwt.clientId and clientSecretRef")
        if self.token_service is None:
            raise ValueError("JWT runtime auth requires token service")

        discovery_url = jwt_config.discoveryUrl or self._extract_discovery_url(metadata, runtime_detail)
        if not discovery_url:
            raise ValueError("JWT runtime discovery URL is unavailable")

        client_secret = await self.token_service.get_federation_secret(
            federation_id=str(federation.id),
            secret_name=jwt_config.clientSecretRef,
        )
        if not client_secret:
            raise ValueError("JWT runtime client secret is missing")

        cache_key = "|".join(
            [
                str(federation.id),
                discovery_url,
                jwt_config.clientId,
                jwt_config.audience or "",
                jwt_config.scope or "",
            ]
        )
        cached = self._jwt_cache.get(cache_key)
        if cached and cached.is_valid():
            return {"Authorization": f"Bearer {cached.access_token}"}, None

        token_endpoint = await self._discover_token_endpoint(discovery_url)
        if not token_endpoint:
            raise ValueError("JWT runtime token endpoint discovery failed")

        tokens = await self.oauth_client.fetch_client_credentials_token(
            token_endpoint=token_endpoint,
            client_id=jwt_config.clientId,
            client_secret=client_secret,
            scope=jwt_config.scope,
            audience=jwt_config.audience,
        )
        if tokens is None or not tokens.access_token:
            raise ValueError("JWT runtime token acquisition failed: no access token returned")

        self._jwt_cache[cache_key] = _CachedOAuthToken(
            access_token=tokens.access_token,
            expires_at=tokens.expires_at,
        )
        return {"Authorization": f"Bearer {tokens.access_token}"}, None

    @staticmethod
    def _extract_discovery_url(metadata: dict[str, Any], runtime_detail: dict[str, Any] | None) -> str | None:
        config = (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration") or {}
        custom = config.get("customJWTAuthorizerConfiguration") or {}
        return custom.get("discoveryUrl")

    async def _discover_token_endpoint(self, discovery_url: str) -> str | None:
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": settings.registry_app_name, "Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            response = await client.get(discovery_url)
            response.raise_for_status()
            payload = response.json()
            token_endpoint = payload.get("token_endpoint")
            return token_endpoint if isinstance(token_endpoint, str) and token_endpoint else None
