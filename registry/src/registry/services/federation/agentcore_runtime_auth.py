from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from registry.core.config import settings
from registry.services.federation.agentcore_clients import AgentCoreClientProvider
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.models.federation import AgentCoreRuntimeAccessConfig

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
        extract_region_from_arn: Callable[[str], str],
    ):
        self.client_provider = client_provider
        self.extract_region_from_arn = extract_region_from_arn

    async def build_runtime_http_auth(
        self,
        *,
        runtime_access: AgentCoreRuntimeAccessConfig | dict[str, Any] | None,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        # Runtime enrichment uses resource-local auth settings because an AWS
        # federation can contain a mix of IAM-backed and JWT-backed runtimes.
        discovered_mode, detection_reason = self._detect_agentcore_data_plane_auth_mode_with_reason(
            metadata=metadata,
            runtime_detail=runtime_detail,
        )
        configured_access = self._configured_runtime_access(
            runtime_access,
            metadata=metadata,
            runtime_detail=runtime_detail,
        )
        configured_mode = configured_access.mode
        runtime_arn = self._resolve_runtime_arn(metadata=metadata, runtime_detail=runtime_detail)

        logger.info(
            "AgentCore runtime auth detection: runtime_arn=%s configured_mode=%s discovered_mode=%s reason=%s explicit_runtime_access=%s authorizer_configuration=%s protocol_configuration=%s",
            runtime_arn,
            configured_mode,
            discovered_mode,
            detection_reason,
            runtime_access is not None,
            self._serialize_log_value(
                (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration")
            ),
            self._serialize_log_value(
                (runtime_detail or {}).get("protocolConfiguration") or metadata.get("protocolConfiguration")
            ),
        )

        if configured_mode == AgentCoreRuntimeAccessMode.IAM and discovered_mode != "IAM":
            logger.warning(
                "AgentCore runtime auth mode mismatch: runtime_arn=%s configured_mode=iam discovered_mode=%s reason=%s authorizer_configuration=%s",
                runtime_arn,
                discovered_mode,
                detection_reason,
                self._serialize_log_value(
                    (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration")
                ),
            )
        if configured_mode == AgentCoreRuntimeAccessMode.JWT and discovered_mode != "JWT":
            logger.warning(
                "AgentCore runtime auth mode mismatch: runtime_arn=%s configured_mode=jwt discovered_mode=%s reason=%s authorizer_configuration=%s",
                runtime_arn,
                discovered_mode,
                detection_reason,
                self._serialize_log_value(
                    (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration")
                ),
            )

        if configured_mode == AgentCoreRuntimeAccessMode.JWT:
            return self._build_jwt_auth(runtime_access=configured_access)

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
        mode, _reason = AgentCoreRuntimeAuthService._detect_agentcore_data_plane_auth_mode_with_reason(
            metadata=metadata,
            runtime_detail=runtime_detail,
        )
        return mode

    @staticmethod
    def _detect_agentcore_data_plane_auth_mode_with_reason(
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        # AgentCore exposes authorizer details only through discovery payloads.
        # Treat empty JWT-shaped shells as IAM; require populated JWT evidence.
        config = (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration") or {}
        if not isinstance(config, dict) or not config:
            return "IAM", "authorizerConfiguration missing or empty"

        authorizer_type = str(config.get("authorizerType") or config.get("type") or "").strip().upper()
        if authorizer_type == "JWT":
            return "JWT", "authorizerType/type explicitly set to JWT"
        if authorizer_type == "OAUTH":
            return "JWT", "authorizerType/type explicitly set to OAUTH"

        for key in ("customJWTAuthorizerConfiguration", "jwtAuthorizerConfiguration"):
            candidate = config.get(key)
            if isinstance(candidate, dict):
                populated = sorted(
                    key_name for key_name, value in candidate.items() if value not in (None, "", [], {}, ())
                )
                if populated:
                    return "JWT", f"{key} contains populated keys: {', '.join(populated)}"

        populated_paths = sorted(AgentCoreRuntimeAuthService._collect_populated_paths(config))
        oauth_like_paths = [
            path
            for path in populated_paths
            if any(
                token in path.lower()
                for token in ("oauth", "jwt", "oidc", "openid", "issuer", "jwks", "audience", "scope", "client")
            )
        ]
        if oauth_like_paths:
            return (
                "JWT",
                f"authorizerConfiguration contains oauth/jwt-like populated paths: {', '.join(oauth_like_paths[:8])}",
            )

        return "IAM", "no populated JWT authorizer configuration found"

    @staticmethod
    def _configured_runtime_access(
        runtime_access: AgentCoreRuntimeAccessConfig | dict[str, Any] | None,
        *,
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None,
    ) -> AgentCoreRuntimeAccessConfig:
        if isinstance(runtime_access, AgentCoreRuntimeAccessConfig):
            return runtime_access
        if isinstance(runtime_access, dict) and runtime_access:
            return AgentCoreRuntimeAccessConfig(**runtime_access)
        return AgentCoreRuntimeAuthService.infer_runtime_access(metadata=metadata, runtime_detail=runtime_detail)

    async def _build_iam_auth(
        self,
        *,
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None,
        region: str,
        assume_role_arn: str | None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        runtime_arn = self._resolve_runtime_arn(metadata=metadata, runtime_detail=runtime_detail)
        resolved_region = region.strip()
        if runtime_arn:
            try:
                resolved_region = self.extract_region_from_arn(runtime_arn)
            except ValueError:
                logger.debug(
                    "Falling back to provided region because runtime ARN is missing or malformed",
                    extra={"runtime_arn": runtime_arn},
                )
        if not resolved_region:
            raise ValueError("Unable to determine runtime region from runtime ARN or provided region")
        credentials_provider = await self.client_provider.get_runtime_credentials_provider(
            resolved_region,
            assume_role_arn,
        )
        if not credentials_provider:
            raise ValueError(f"Failed to initialize runtime credentials provider for region {resolved_region}")
        return {}, _SigV4HttpxAuth("bedrock-agentcore", resolved_region, credentials_provider)

    def _build_jwt_auth(
        self, *, runtime_access: AgentCoreRuntimeAccessConfig
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        jwt_config = runtime_access.jwt

        if jwt_config is None:
            raise ValueError("JWT runtime requires resource-level runtimeAccess.jwt configuration")

        audience_claim = settings.jwt_audience

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

        # Derive the issuer from discoveryUrl so the iss claim matches what
        # AgentCore's JWT authorizer expects (the base URL of the OIDC endpoint),
        # regardless of what AUTH_SERVER_EXTERNAL_URL is set to locally.
        discovery_url = jwt_config.discoveryUrl
        if discovery_url:
            parsed = urlparse(discovery_url)
            issuer = f"{parsed.scheme}://{parsed.netloc}"
        else:
            issuer = settings.jwt_issuer

        payload = build_jwt_payload(
            subject=settings.registry_app_name,
            issuer=issuer,
            audience=audience_claim,
            expires_in_seconds=_JWT_EXPIRES_IN_SECONDS,
            extra_claims=extra_claims or None,
        )
        token = encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)
        return {"Authorization": f"Bearer {token}"}, None

    @staticmethod
    def infer_runtime_access(
        *,
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None = None,
    ) -> AgentCoreRuntimeAccessConfig:
        mode, _reason = AgentCoreRuntimeAuthService._detect_agentcore_data_plane_auth_mode_with_reason(
            metadata=metadata,
            runtime_detail=runtime_detail,
        )
        config = (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration") or {}
        payload: dict[str, Any] = {
            "mode": mode.lower(),
            "iam": {},
        }
        if mode == "JWT":
            payload["jwt"] = {}
            discovery_url = AgentCoreRuntimeAuthService._extract_jwt_discovery_url(config)
            if discovery_url:
                payload["jwt"]["discoveryUrl"] = discovery_url
            allowed_audiences = AgentCoreRuntimeAuthService._extract_jwt_allowed_audiences(config)
            if allowed_audiences:
                payload["jwt"]["audiences"] = allowed_audiences
        return AgentCoreRuntimeAccessConfig(**payload)

    @staticmethod
    def _extract_jwt_discovery_url(authorizer_config: dict[str, Any]) -> str | None:
        if not isinstance(authorizer_config, dict):
            return None
        for key in ("customJWTAuthorizer", "customJWTAuthorizerConfiguration", "jwtAuthorizerConfiguration"):
            candidate = authorizer_config.get(key)
            if isinstance(candidate, dict):
                discovery_url = candidate.get("discoveryUrl")
                if isinstance(discovery_url, str) and discovery_url:
                    return discovery_url
        for path, value in AgentCoreRuntimeAuthService._walk_paths(authorizer_config):
            if path.lower().endswith("discoveryurl") and isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _extract_jwt_allowed_audiences(authorizer_config: dict[str, Any]) -> list[str]:
        if not isinstance(authorizer_config, dict):
            return []
        for key in ("customJWTAuthorizer", "customJWTAuthorizerConfiguration", "jwtAuthorizerConfiguration"):
            candidate = authorizer_config.get(key)
            if isinstance(candidate, dict):
                audiences = candidate.get("allowedAudience") or candidate.get("audiences") or []
                if isinstance(audiences, list):
                    return [a for a in audiences if isinstance(a, str) and a]
                if isinstance(audiences, str) and audiences:
                    return [audiences]
        # Fall back to path-based search for any audience-like populated string list
        for path, value in AgentCoreRuntimeAuthService._walk_paths(authorizer_config):
            lower_path = path.lower()
            if ("audience" in lower_path) and isinstance(value, str) and value:
                return [value]
        return []

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

    @staticmethod
    def _walk_paths(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
        if isinstance(value, dict):
            items: list[tuple[str, Any]] = []
            for key, child in value.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                items.extend(AgentCoreRuntimeAuthService._walk_paths(child, child_prefix))
            return items
        if isinstance(value, list):
            items: list[tuple[str, Any]] = []
            for index, child in enumerate(value):
                child_prefix = f"{prefix}[{index}]"
                items.extend(AgentCoreRuntimeAuthService._walk_paths(child, child_prefix))
            return items
        return [(prefix, value)]

    @staticmethod
    def _collect_populated_paths(value: Any) -> list[str]:
        return [
            path
            for path, child in AgentCoreRuntimeAuthService._walk_paths(value)
            if path and child not in (None, "", [], {}, ())
        ]

    @staticmethod
    def _serialize_log_value(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            return repr(value)
