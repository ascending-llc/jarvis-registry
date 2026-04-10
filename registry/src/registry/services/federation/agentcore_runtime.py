import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from registry.core.config import settings
from registry.core.mcp_client import MCPServerData, get_tools_and_capabilities_from_server
from registry_pkgs.models import A2AAgent, ExtendedMCPServer

from .agentcore_clients import AgentCoreClientProvider

logger = logging.getLogger(__name__)


class _SigV4HttpxAuth(httpx.Auth):
    """HTTPX auth provider that signs each request using AWS SigV4."""

    requires_request_body = True

    def __init__(self, service: str, region: str, credentials_provider: Callable[[], Any]):
        self.service = service
        self.region = region
        self.credentials_provider = credentials_provider

    def auth_flow(self, request: httpx.Request):
        """
        Sign each outgoing HTTPX request with a frozen snapshot of the current
        AWS credentials and write the SigV4 headers back onto the request.
        """
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


class AgentCoreRuntimeInvoker:
    """
    AgentCore Runtime data-plane invoker.

    Official API mapping used by this class:
    - MCP capability discovery uses `InvokeAgentRuntime` and MCP JSON-RPC methods
      such as `initialize`, `tools/list`, `resources/list`, and `prompts/list`.
    - A2A card discovery uses `GetAgentCard`.

    IAM and JWT execution paths stay separate so federation discovery code can
    remain focused on model transformation instead of transport details.
    """

    def __init__(
        self,
        *,
        client_provider: AgentCoreClientProvider,
        extract_region_from_arn: Callable[[str, str], str],
    ):
        self.client_provider = client_provider
        self.extract_region_from_arn = extract_region_from_arn
        self._invoke_runtime_retry_attempts = max(1, int(settings.agentcore_invoke_runtime_retry_attempts or 4))
        self._invoke_runtime_retry_delay_seconds = float(settings.agentcore_invoke_runtime_retry_delay_seconds or 5.0)
        self._get_agent_card_retry_attempts = max(1, int(settings.agentcore_get_agent_card_retry_attempts or 1))
        self._get_agent_card_retry_delay_seconds = float(settings.agentcore_get_agent_card_retry_delay_seconds or 3.0)

    async def enrich_mcp_server(
        self,
        *,
        server: ExtendedMCPServer,
        region: str,
        assume_role_arn: str | None = None,
    ) -> None:
        config = server.config or {}
        runtime_url = config.get("url")
        if not runtime_url:
            return

        try:
            result = await self.fetch_mcp_runtime_capabilities(
                runtime_url=runtime_url,
                transport_type=config.get("type"),
                metadata=server.federationMetadata or {},
                runtime_detail=server.federationMetadata or {},
                region=region,
                assume_role_arn=assume_role_arn,
            )
        except Exception as exc:
            logger.warning("MCP runtime enrichment failed for %s: %s", server.serverName, exc)
            metadata = dict(server.federationMetadata or {})
            self._set_enrichment_error(metadata, f"mcp enrichment failed: {exc}")
            server.federationMetadata = metadata
            return

        if result.error_message:
            logger.warning("MCP runtime enrichment returned error for %s: %s", server.serverName, result.error_message)
            metadata = dict(server.federationMetadata or {})
            self._set_enrichment_error(metadata, result.error_message)
            server.federationMetadata = metadata
            return

        tools = result.tools or []
        config["toolFunctions"] = self._convert_tools_to_tool_functions(tools, server.serverName)
        config["tools"] = ", ".join(tool.get("name", "") for tool in tools if tool.get("name"))
        config["resources"] = result.resources or []
        config["prompts"] = result.prompts or []
        config["capabilities"] = (
            json.dumps(result.capabilities or {}, ensure_ascii=False) if result.capabilities else "{}"
        )
        config["requiresInit"] = bool(result.requires_init) if result.requires_init is not None else False
        server.config = config
        server.numTools = len(tools)

        metadata = dict(server.federationMetadata or {})
        self._set_enrichment_error(metadata, None)
        server.federationMetadata = metadata

    async def enrich_a2a_agent(
        self,
        *,
        agent: A2AAgent,
        runtime_detail: dict[str, Any],
        region: str,
        assume_role_arn: str | None = None,
    ) -> None:
        """
        docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_GetAgentCard.html
        """
        runtime_arn = self._resolve_runtime_arn(
            metadata=agent.federationMetadata or {},
            runtime_detail=runtime_detail,
        )
        if not runtime_arn:
            logger.warning(
                "Skipping A2A runtime enrichment for %s: missing runtime ARN. runtime_detail_keys=%s metadata_keys=%s",
                agent.card.name,
                sorted(runtime_detail.keys()),
                sorted((agent.federationMetadata or {}).keys()),
            )
            if agent.wellKnown:
                agent.wellKnown.lastSyncStatus = "failed"
                agent.wellKnown.syncError = "missing runtime ARN for A2A enrichment"
                agent.wellKnown.lastSyncAt = datetime.now(UTC)
            metadata = dict(agent.federationMetadata or {})
            self._set_enrichment_error(metadata, "a2a enrichment failed: missing runtime ARN")
            agent.federationMetadata = metadata
            return

        escaped_runtime_arn = quote(runtime_arn, safe="")
        card_url = (
            f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations"
            f"/.well-known/agent-card.json?qualifier=DEFAULT"
        )

        try:
            card_data = await self.fetch_a2a_agent_card(
                card_url=card_url,
                metadata=agent.federationMetadata or {},
                runtime_detail=runtime_detail,
                region=region,
                assume_role_arn=assume_role_arn,
            )
        except Exception as exc:
            logger.warning("A2A runtime enrichment failed for %s: %s", agent.card.name, exc)
            if agent.wellKnown:
                agent.wellKnown.lastSyncStatus = "failed"
                agent.wellKnown.syncError = str(exc)
                agent.wellKnown.lastSyncAt = datetime.now(UTC)
            metadata = dict(agent.federationMetadata or {})
            self._set_enrichment_error(metadata, f"a2a enrichment failed: {exc}")
            agent.federationMetadata = metadata
            return

        card_payload = self._extract_a2a_card_payload(card_data)
        fallback_card = agent.card.model_dump(mode="json")
        merged = {**fallback_card, **card_payload, "url": fallback_card.get("url")}

        refreshed = A2AAgent.from_a2a_agent_card(
            card_data=merged,
            path=agent.path,
            author=agent.author,
            isEnabled=agent.isEnabled,
            status=agent.status,
            tags=agent.tags,
            registeredBy=agent.registeredBy,
            registeredAt=agent.registeredAt,
            federationRefId=agent.federationRefId,
            federationMetadata=agent.federationMetadata,
            wellKnown=agent.wellKnown.model_dump(mode="json") if agent.wellKnown else None,
        )

        agent.card = refreshed.card
        if agent.wellKnown:
            agent.wellKnown.lastSyncStatus = "success"
            agent.wellKnown.syncError = None
            agent.wellKnown.lastSyncAt = datetime.now(UTC)
            agent.wellKnown.lastSyncVersion = str(agent.card.version)

        metadata = dict(agent.federationMetadata or {})
        self._set_enrichment_error(metadata, None)
        agent.federationMetadata = metadata

    @staticmethod
    def detect_agentcore_data_plane_auth_mode(
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None = None,
    ) -> str:
        """
        Detect runtime data-plane auth mode from authorizer configuration.
        Defaults to IAM when not explicitly JWT.
        """
        config = (runtime_detail or {}).get("authorizerConfiguration") or metadata.get("authorizerConfiguration") or {}
        text = json.dumps(config, default=str).upper()
        if "JWT" in text:
            return "JWT"
        return "IAM"

    async def fetch_mcp_runtime_capabilities(
        self,
        *,
        runtime_url: str,
        transport_type: str | None,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> MCPServerData:
        """
        Discover MCP tools/resources/prompts for an AgentCore runtime.

        Official API mapping:
        - IAM path: `InvokeAgentRuntime` with MCP JSON-RPC methods
        - JWT path: HTTP call to the runtime invocation endpoint
        """
        mode = self.detect_agentcore_data_plane_auth_mode(metadata=metadata, runtime_detail=runtime_detail)
        if mode == "IAM":
            sdk_result = await self._invoke_mcp_runtime_via_sdk(
                metadata=metadata,
                runtime_detail=runtime_detail,
                region=region,
                assume_role_arn=assume_role_arn,
            )
            if not sdk_result.error_message:
                return sdk_result

            # Fallback path for IAM: use SigV4-authenticated MCP transport over HTTP.
            http_fallback = await self._fetch_mcp_runtime_capabilities_via_http_with_retry(
                runtime_url=runtime_url,
                transport_type=transport_type,
                metadata=metadata,
                runtime_detail=runtime_detail,
                region=region,
                assume_role_arn=assume_role_arn,
            )
            if not http_fallback.error_message:
                return http_fallback
            return sdk_result

        return await self._fetch_mcp_runtime_capabilities_via_http_with_retry(
            runtime_url=runtime_url,
            transport_type=transport_type,
            metadata=metadata,
            runtime_detail=runtime_detail,
            region=region,
            assume_role_arn=assume_role_arn,
        )

    async def invoke_runtime_prompt(
        self,
        *,
        runtime_arn: str,
        region: str,
        prompt: str = "ping",
        qualifier: str = "DEFAULT",
        assume_role_arn: str | None = None,
    ) -> dict[str, Any]:
        """
        Directly invoke runtime with a simple prompt payload for smoke testing.
        """
        if not runtime_arn:
            raise ValueError("runtime_arn is required")

        resolved_region = self.extract_region_from_arn(runtime_arn, region)
        client = await self._get_runtime_client(resolved_region, assume_role_arn)

        payload = self._json_to_bytes({"prompt": prompt})
        response = await self._call_with_runtime_init_retry(
            lambda: client.invoke_agent_runtime(
                agentRuntimeArn=runtime_arn,
                qualifier=qualifier,
                payload=payload,
            )
        )

        response_text = self._response_text_from_runtime_response(response)
        response_json = self._extract_json_payload(response_text)

        return {
            "runtimeArn": runtime_arn,
            "qualifier": qualifier,
            "statusCode": response.get("statusCode"),
            "contentType": response.get("contentType"),
            "runtimeSessionId": response.get("runtimeSessionId"),
            "mcpSessionId": response.get("mcpSessionId"),
            "mcpProtocolVersion": response.get("mcpProtocolVersion"),
            "responseJson": response_json or None,
            "responseText": response_text if not response_json else None,
        }

    async def fetch_a2a_agent_card(
        self,
        *,
        card_url: str,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch the A2A agent card for an AgentCore runtime.

        Official API mapping:
        - IAM path: `GetAgentCard`
        - JWT path: HTTP GET on `/.well-known/agent-card.json`
        """
        mode = self.detect_agentcore_data_plane_auth_mode(metadata=metadata, runtime_detail=runtime_detail)
        if mode == "IAM":
            return await self._get_a2a_agent_card_via_sdk(
                metadata=metadata,
                runtime_detail=runtime_detail,
                region=region,
                assume_role_arn=assume_role_arn,
            )

        headers, httpx_auth = await self._build_runtime_http_auth(
            metadata=metadata,
            runtime_detail=runtime_detail,
            region=region,
            assume_role_arn=assume_role_arn,
        )
        async with httpx.AsyncClient(
            timeout=settings.agentcore_jwt_http_timeout_seconds,
            headers=headers,
            auth=httpx_auth,
        ) as client:
            response = await client.get(card_url)
            response.raise_for_status()
            return response.json()

    async def _fetch_mcp_runtime_capabilities_via_http(
        self,
        *,
        runtime_url: str,
        transport_type: str | None,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> MCPServerData:
        headers, httpx_auth = await self._build_runtime_http_auth(
            metadata=metadata,
            runtime_detail=runtime_detail,
            region=region,
            assume_role_arn=assume_role_arn,
        )
        return await get_tools_and_capabilities_from_server(
            runtime_url,
            headers=headers,
            transport_type=transport_type,
            include_resources=True,
            include_prompts=True,
            httpx_auth=httpx_auth,
        )

    async def _fetch_mcp_runtime_capabilities_via_http_with_retry(
        self,
        *,
        runtime_url: str,
        transport_type: str | None,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> MCPServerData:
        last_result: MCPServerData | None = None
        for attempt in range(1, self._invoke_runtime_retry_attempts + 1):
            result = await self._fetch_mcp_runtime_capabilities_via_http(
                runtime_url=runtime_url,
                transport_type=transport_type,
                metadata=metadata,
                runtime_detail=runtime_detail,
                region=region,
                assume_role_arn=assume_role_arn,
            )
            last_result = result
            if not result.error_message or not self._is_runtime_init_timeout_text(result.error_message):
                return result
            if attempt < self._invoke_runtime_retry_attempts:
                await asyncio.sleep(self._invoke_runtime_retry_delay_seconds * attempt)
        return last_result or MCPServerData(None, None, None, None, "MCP HTTP retry failed")

    async def _get_a2a_agent_card_via_sdk(
        self,
        *,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> dict[str, Any]:
        runtime_arn = self._resolve_runtime_arn(metadata=metadata, runtime_detail=runtime_detail)
        if not runtime_arn:
            raise ValueError("Missing runtime ARN for GetAgentCard")

        resolved_region = self.extract_region_from_arn(runtime_arn, region)
        client = await self._get_runtime_client(resolved_region, assume_role_arn)
        response = await self._call_with_a2a_card_retry(
            lambda: client.get_agent_card(agentRuntimeArn=runtime_arn, qualifier="DEFAULT")
        )
        card = self._coerce_json_object(response.get("agentCard"))
        if card is None:
            raise ValueError("GetAgentCard returned unexpected payload")
        return card

    async def _invoke_mcp_runtime_via_sdk(
        self,
        *,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> MCPServerData:
        runtime_arn = self._resolve_runtime_arn(metadata=metadata, runtime_detail=runtime_detail)
        if not runtime_arn:
            return MCPServerData(None, None, None, None, "Missing runtime ARN for InvokeAgentRuntime")

        resolved_region = self.extract_region_from_arn(runtime_arn, region)
        client = await self._get_runtime_client(resolved_region, assume_role_arn)

        try:
            runtime_session_id, mcp_session_id, protocol_version = await self._initialize_mcp_session(
                client=client,
                runtime_arn=runtime_arn,
            )
            tools_result, _, _, _ = await self._invoke_mcp_jsonrpc(
                client=client,
                runtime_arn=runtime_arn,
                method="tools/list",
                params={},
                request_id=self._next_request_id(),
                runtime_session_id=runtime_session_id,
                mcp_session_id=mcp_session_id,
                protocol_version=protocol_version,
            )
            resources_result, _, _, _ = await self._invoke_mcp_jsonrpc(
                client=client,
                runtime_arn=runtime_arn,
                method="resources/list",
                params={},
                request_id=self._next_request_id(),
                runtime_session_id=runtime_session_id,
                mcp_session_id=mcp_session_id,
                protocol_version=protocol_version,
            )
            prompts_result, _, _, _ = await self._invoke_mcp_jsonrpc(
                client=client,
                runtime_arn=runtime_arn,
                method="prompts/list",
                params={},
                request_id=self._next_request_id(),
                runtime_session_id=runtime_session_id,
                mcp_session_id=mcp_session_id,
                protocol_version=protocol_version,
            )
        except Exception as exc:
            return MCPServerData(None, None, None, None, f"SDK MCP invocation failed: {exc}")

        tools = self._normalize_mcp_tools(tools_result.get("tools", []))
        resources = self._normalize_mcp_resources(resources_result.get("resources", []))
        prompts = self._normalize_mcp_prompts(prompts_result.get("prompts", []))
        capabilities = {"sdkInvokeMode": "invoke_agent_runtime"}
        return MCPServerData(
            tools=tools,
            resources=resources,
            prompts=prompts,
            capabilities=capabilities,
            requires_init=bool(runtime_session_id or mcp_session_id),
            error_message=None,
        )

    async def _get_runtime_client(self, region: str, assume_role_arn: str | None = None) -> Any:
        client = await self.client_provider.get_runtime_client(region, assume_role_arn)
        if not client:
            raise ValueError(f"Failed to initialize AgentCore runtime client for region {region}")
        return client

    async def _initialize_mcp_session(
        self,
        *,
        client: Any,
        runtime_arn: str,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Establish the MCP session explicitly and obtain the session identifiers
        from the initialize response before issuing list/call methods.
        """
        (
            init_result,
            runtime_session_id,
            mcp_session_id,
            protocol_version,
        ) = await self._call_with_runtime_init_retry_async(
            lambda: self._invoke_mcp_jsonrpc(
                client=client,
                runtime_arn=runtime_arn,
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-gateway-registry", "version": "1.0.0"},
                },
                request_id=self._next_request_id(),
                runtime_session_id=None,
                mcp_session_id=None,
                protocol_version=None,
            )
        )
        negotiated_protocol_version = init_result.get("protocolVersion") or protocol_version or "2024-11-05"

        await self._send_mcp_notification(
            client=client,
            runtime_arn=runtime_arn,
            method="notifications/initialized",
            params={},
            runtime_session_id=runtime_session_id,
            mcp_session_id=mcp_session_id,
            protocol_version=negotiated_protocol_version,
        )
        return runtime_session_id, mcp_session_id, negotiated_protocol_version

    async def _invoke_mcp_jsonrpc(
        self,
        *,
        client: Any,
        runtime_arn: str,
        method: str,
        params: dict[str, Any],
        request_id: str | int,
        runtime_session_id: str | None,
        mcp_session_id: str | None,
        protocol_version: str | None,
    ) -> tuple[dict[str, Any], str | None, str | None, str | None]:
        kwargs = self._build_runtime_invoke_kwargs(
            runtime_arn=runtime_arn,
            payload={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            runtime_session_id=runtime_session_id,
            mcp_session_id=mcp_session_id,
            protocol_version=protocol_version,
        )

        try:
            response = await asyncio.to_thread(client.invoke_agent_runtime, **kwargs)
        except Exception as exc:
            raise ValueError(
                "invoke_agent_runtime failed "
                f"(method={method}, runtime_arn={runtime_arn}, qualifier=DEFAULT, accept={kwargs['accept']}, "
                f"contentType={kwargs['contentType']}): {exc}"
            ) from exc

        response_text = self._response_text_from_runtime_response(response)
        response_payload = self._extract_json_payload(response_text)
        if response_text and not response_payload:
            raise ValueError(
                "invoke_agent_runtime returned non-JSON response "
                f"(method={method}, statusCode={response.get('statusCode')}, "
                f"contentType={response.get('contentType')}, body={response_text[:1000]})"
            )

        if response_payload.get("error"):
            raise ValueError(
                "MCP method failed "
                f"(method={method}, statusCode={response.get('statusCode')}, contentType={response.get('contentType')}, "
                f"error={response_payload['error']}, body={response_text[:1000]})"
            )

        return (
            response_payload.get("result", {}),
            response.get("runtimeSessionId") or runtime_session_id,
            response.get("mcpSessionId") or mcp_session_id,
            response.get("mcpProtocolVersion") or protocol_version,
        )

    @staticmethod
    def _next_request_id() -> str:
        """
        Generate a JSON-RPC request id that is unique across concurrent requests
        and across service instances.
        """
        return uuid.uuid4().hex

    async def _send_mcp_notification(
        self,
        *,
        client: Any,
        runtime_arn: str,
        method: str,
        params: dict[str, Any],
        runtime_session_id: str | None,
        mcp_session_id: str | None,
        protocol_version: str | None,
    ) -> None:
        kwargs = self._build_runtime_invoke_kwargs(
            runtime_arn=runtime_arn,
            payload={
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            },
            runtime_session_id=runtime_session_id,
            mcp_session_id=mcp_session_id,
            protocol_version=protocol_version,
        )

        try:
            response = await asyncio.to_thread(client.invoke_agent_runtime, **kwargs)
        except Exception as exc:
            raise ValueError(
                "invoke_agent_runtime notification failed "
                f"(method={method}, runtime_arn={runtime_arn}, qualifier=DEFAULT): {exc}"
            ) from exc

        response_text = self._response_text_from_runtime_response(response)
        response_payload = self._extract_json_payload(response_text)
        if response_payload.get("error"):
            raise ValueError(
                "MCP notification failed "
                f"(method={method}, statusCode={response.get('statusCode')}, contentType={response.get('contentType')}, "
                f"error={response_payload['error']}, body={response_text[:1000]})"
            )

    @staticmethod
    def _extract_json_payload(response_text: str) -> dict[str, Any]:
        """
        Parse JSON payload from direct JSON or SSE-like "data: {...}" envelope.
        """
        if not response_text:
            return {}

        # Handle text/event-stream style chunks.
        for line in response_text.splitlines():
            data_line = line.strip()
            if data_line.startswith("data:"):
                parsed = AgentCoreRuntimeInvoker._coerce_json_object(data_line[5:].strip())
                if parsed is not None:
                    return parsed

        # Direct JSON response.
        parsed = AgentCoreRuntimeInvoker._coerce_json_object(response_text)
        if parsed is not None:
            return parsed

        # Fallback: find first JSON object in mixed text.
        start = response_text.find("{")
        if start >= 0:
            parsed = AgentCoreRuntimeInvoker._coerce_json_object(response_text[start:])
            if parsed is not None:
                return parsed
            else:
                logger.debug("Unable to parse runtime response as JSON payload: %s", response_text[:1000])

        return {}

    @staticmethod
    def _json_to_bytes(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _coerce_json_object(candidate: Any) -> dict[str, Any] | None:
        if isinstance(candidate, dict):
            return candidate
        if not isinstance(candidate, str) or not candidate:
            return None
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _response_text_from_runtime_response(cls, response: dict[str, Any]) -> str:
        response_bytes = cls._read_response_blob(response.get("response"))
        return response_bytes.decode("utf-8", errors="replace") if response_bytes else ""

    def _build_runtime_invoke_kwargs(
        self,
        *,
        runtime_arn: str,
        payload: dict[str, Any],
        runtime_session_id: str | None,
        mcp_session_id: str | None,
        protocol_version: str | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "agentRuntimeArn": runtime_arn,
            "qualifier": "DEFAULT",
            "contentType": "application/json",
            "accept": "application/json, text/event-stream",
            "payload": self._json_to_bytes(payload),
        }
        if runtime_session_id:
            kwargs["runtimeSessionId"] = runtime_session_id
        if mcp_session_id:
            kwargs["mcpSessionId"] = mcp_session_id
        if protocol_version:
            kwargs["mcpProtocolVersion"] = protocol_version
        return kwargs

    async def _call_with_runtime_init_retry(self, operation: Callable[[], Any]) -> Any:
        """
        Retry transient runtime initialization timeout errors from AgentCore runtime.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._invoke_runtime_retry_attempts + 1):
            try:
                return await asyncio.to_thread(operation)
            except Exception as exc:
                last_exc = exc
                if not self._is_runtime_init_timeout_error(exc) or attempt == self._invoke_runtime_retry_attempts:
                    raise
                await asyncio.sleep(self._invoke_runtime_retry_delay_seconds * attempt)
        if last_exc:
            raise last_exc
        raise RuntimeError("retry operation failed without exception")

    async def _call_with_runtime_init_retry_async(self, operation: Callable[[], Any]) -> Any:
        """
        Retry transient runtime initialization timeout errors for async operations.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._invoke_runtime_retry_attempts + 1):
            try:
                return await operation()
            except Exception as exc:
                last_exc = exc
                if not self._is_runtime_init_timeout_error(exc) or attempt == self._invoke_runtime_retry_attempts:
                    raise
                await asyncio.sleep(self._invoke_runtime_retry_delay_seconds * attempt)
        if last_exc:
            raise last_exc
        raise RuntimeError("retry async operation failed without exception")

    async def _call_with_a2a_card_retry(self, operation: Callable[[], Any]) -> Any:
        """
        Retry A2A card fetch for known transient runtime-side failures.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._get_agent_card_retry_attempts + 1):
            try:
                return await asyncio.to_thread(operation)
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable_a2a_card_error(exc) or attempt == self._get_agent_card_retry_attempts:
                    raise
                logger.warning(
                    "Retrying A2A GetAgentCard after transient runtime error (attempt=%s/%s, backoff_seconds=%s): %s",
                    attempt,
                    self._get_agent_card_retry_attempts,
                    self._get_agent_card_retry_delay_seconds * attempt,
                    exc,
                )
                await asyncio.sleep(self._get_agent_card_retry_delay_seconds * attempt)
        if last_exc:
            raise last_exc
        raise RuntimeError("A2A card retry operation failed without exception")

    @staticmethod
    def _is_runtime_init_timeout_error(exc: Exception) -> bool:
        message = str(exc)
        return "Runtime initialization time exceeded" in message

    @staticmethod
    def _is_runtime_init_timeout_text(message: str) -> bool:
        return "Runtime initialization time exceeded" in message

    @staticmethod
    def _is_retryable_a2a_card_error(exc: Exception) -> bool:
        message = str(exc)
        if "Runtime initialization time exceeded" in message:
            return True
        if "GetAgentCard operation" in message and "(502)" in message:
            return True
        return "agent card endpoint" in message and "(502)" in message

    @staticmethod
    def _read_response_blob(blob: Any) -> bytes:
        if blob is None:
            return b""
        if isinstance(blob, (bytes, bytearray)):
            return bytes(blob)
        if hasattr(blob, "read"):
            return blob.read()
        return b""

    @staticmethod
    def _normalize_mcp_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for tool in tools or []:
            normalized.append(
                {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                }
            )
        return normalized

    @staticmethod
    def _normalize_mcp_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for resource in resources or []:
            normalized.append(
                {
                    "uri": resource.get("uri"),
                    "name": resource.get("name"),
                    "description": resource.get("description"),
                    "mimeType": resource.get("mimeType"),
                    "annotations": resource.get("annotations"),
                }
            )
        return normalized

    @staticmethod
    def _normalize_mcp_prompts(prompts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for prompt in prompts or []:
            normalized.append(
                {
                    "name": prompt.get("name"),
                    "description": prompt.get("description"),
                    "arguments": prompt.get("arguments") or [],
                }
            )
        return normalized

    @staticmethod
    def _extract_a2a_card_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        if isinstance(payload.get("agentCard"), dict):
            return payload["agentCard"]
        if isinstance(payload.get("card"), dict):
            return payload["card"]
        return payload

    @staticmethod
    def _resolve_runtime_arn(
        *,
        metadata: dict[str, Any] | None,
        runtime_detail: dict[str, Any] | None,
    ) -> str | None:
        """
        Resolve the canonical runtime ARN used by service-layer execution.

        Internal convention prefers `runtimeArn`.
        `agentRuntimeArn` is accepted only as a compatibility bridge for raw SDK payloads.
        """
        detail = runtime_detail or {}
        meta = metadata or {}
        return (
            detail.get("runtimeArn")
            or meta.get("runtimeArn")
            or detail.get("agentRuntimeArn")
            or meta.get("agentRuntimeArn")
        )

    @staticmethod
    def _convert_tools_to_tool_functions(tool_list: list[dict[str, Any]], server_name: str) -> dict[str, Any]:
        tool_functions: dict[str, Any] = {}
        server_suffix = "".join(ch for ch in server_name.lower() if ch.isalnum() or ch == "_")

        for tool in tool_list:
            tool_name = str(tool.get("name", "")).strip()
            if not tool_name:
                continue
            function_name = f"{tool_name}_mcp_{server_suffix}"
            tool_functions[function_name] = {
                "type": "function",
                "function": {
                    "name": function_name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
                "mcpToolName": tool_name,
            }
        return tool_functions

    @staticmethod
    def _set_enrichment_error(metadata: dict[str, Any] | None, error_message: str | None) -> None:
        if metadata is None:
            return
        metadata["enrichedAt"] = datetime.now(UTC)
        metadata["enrichmentError"] = error_message

    async def _build_runtime_http_auth(
        self,
        *,
        metadata: dict[str, Any],
        region: str,
        runtime_detail: dict[str, Any] | None = None,
        assume_role_arn: str | None = None,
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        """
        Build authentication material for runtime data-plane requests.

        Modes:
        - IAM: SigV4 request signing
        - JWT: Bearer token header
        """
        mode = self.detect_agentcore_data_plane_auth_mode(metadata=metadata, runtime_detail=runtime_detail)
        if mode == "JWT":
            token = settings.agentcore_runtime_jwt
            if not token:
                raise ValueError("Runtime auth mode JWT detected but no AGENTCORE_RUNTIME_JWT token was configured")
            return {"Authorization": f"Bearer {token}"}, None

        resolved_region = self.extract_region_from_arn(metadata.get("runtimeArn", ""), region)
        credentials_provider = await self.client_provider.get_runtime_credentials_provider(
            resolved_region,
            assume_role_arn,
        )
        if not credentials_provider:
            raise ValueError(f"Failed to initialize runtime credentials provider for region {resolved_region}")
        return {}, _SigV4HttpxAuth("bedrock-agentcore", resolved_region, credentials_provider)
