from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from registry.core.config import settings
from registry.services.federation.agentcore_clients import AgentCoreClientProvider
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.models.federation import AwsAgentCoreProviderConfig, Federation

logger = logging.getLogger(__name__)

_JWT_EXPIRES_IN_SECONDS = 300


class _SigV4HttpxAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, service: str, region: str, credentials_provider: Callable[[], Any]):
        self.service = service
        self.region = region
        self.credentials_provider = credentials_provider

    def auth_flow(self, request: httpx.Request):
        # HTTP fallback requests must be signed the same way as SDK runtime calls.
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


class AgentCoreRuntimeAuthService:
    def __init__(
        self,
        *,
        client_provider: AgentCoreClientProvider,
        extract_region_from_arn: Callable[[str, str], str],
    ):
        self.client_provider = client_provider
        self.extract_region_from_arn = extract_region_from_arn

    async def build_runtime_http_auth(
        self,
        *,
        federation: Federation | None,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        # Discovery remains IAM-only, but runtime enrichment must enforce the
        # federation's explicit data-plane mode to avoid silently using the
        # wrong auth path against a differently configured runtime.
        discovered_mode = self.detect_agentcore_data_plane_auth_mode(metadata=metadata, runtime_detail=runtime_detail)
        if federation is None and discovered_mode == "JWT":
            raise ValueError("JWT runtime auth requires federation context")
        configured_mode = self._configured_mode(federation)

        if configured_mode == AgentCoreRuntimeAccessMode.IAM and discovered_mode != "IAM":
            raise ValueError("Federation runtimeAccess.mode=iam but discovered runtime auth mode is JWT")
        if configured_mode == AgentCoreRuntimeAccessMode.JWT and discovered_mode != "JWT":
            raise ValueError("Federation runtimeAccess.mode=jwt but discovered runtime auth mode is IAM")

        if configured_mode == AgentCoreRuntimeAccessMode.JWT:
            return self._build_jwt_auth(federation=federation)

        return await self._build_iam_auth(
            metadata=metadata,
            runtime_detail=runtime_detail,
            region=region,
            assume_role_arn=assume_role_arn,
        )

    @staticmethod
    def detect_agentcore_data_plane_auth_mode(
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None = None,
    ) -> str:
        # AgentCore only exposes authorizer details in discovery metadata, so
        # the runtime path infers JWT-vs-IAM from that payload instead of from
        # a dedicated enum field.
        config = (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration") or {}
        text = json.dumps(config, default=str).upper()
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
        runtime_detail: dict[str, Any] | None,
        region: str,
        assume_role_arn: str | None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        runtime_arn = self._resolve_runtime_arn(metadata=metadata, runtime_detail=runtime_detail)
        resolved_region = self.extract_region_from_arn(runtime_arn or "", region)
        credentials_provider = await self.client_provider.get_runtime_credentials_provider(
            resolved_region,
            assume_role_arn,
        )
        if not credentials_provider:
            raise ValueError(f"Failed to initialize runtime credentials provider for region {resolved_region}")
        return {}, _SigV4HttpxAuth("bedrock-agentcore", resolved_region, credentials_provider)

    def _build_jwt_auth(self, *, federation: Federation | None) -> tuple[dict[str, str], httpx.Auth | None]:
        if federation is None:
            raise ValueError("JWT runtime auth requires federation context")

        provider_config = AwsAgentCoreProviderConfig(**dict(federation.providerConfig or {}))
        jwt_config = provider_config.runtimeAccess.jwt
        if jwt_config is None:
            raise ValueError("JWT runtime requires providerConfig.runtimeAccess.jwt configuration")

        audiences = list(jwt_config.audiences or [])
        audience_claim: str | list[str] = settings.jwt_audience
        if audiences:
            audience_claim = audiences[0] if len(audiences) == 1 else audiences

        # The runtime authorizer may validate client_id / scope claims even
        # though we are not using OAuth. We synthesize those claims directly
        # from federation config when present.
        extra_claims: dict[str, Any] = {}
        if jwt_config.allowedClients:
            extra_claims["client_id"] = jwt_config.allowedClients[0]
        if jwt_config.allowedScopes:
            extra_claims["scope"] = " ".join(jwt_config.allowedScopes)
        if jwt_config.customClaims:
            extra_claims.update(jwt_config.customClaims)

        payload = build_jwt_payload(
            subject=settings.registry_app_name,
            issuer=settings.jwt_issuer,
            audience=audience_claim,  # type: ignore[arg-type]
            expires_in_seconds=_JWT_EXPIRES_IN_SECONDS,
            extra_claims=extra_claims or None,
        )
        token = encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)
        return {"Authorization": f"Bearer {token}"}, None

    @staticmethod
    def _resolve_runtime_arn(
        *,
        metadata: dict[str, Any] | None,
        runtime_detail: dict[str, Any] | None,
    ) -> str | None:
        detail = runtime_detail or {}
        meta = metadata or {}
        return (
            detail.get("runtimeArn")
            or meta.get("runtimeArn")
            or detail.get("agentRuntimeArn")
            or meta.get("agentRuntimeArn")
        )
