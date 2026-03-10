import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import boto3
from beanie import PydanticObjectId

from registry.constants import REGISTRY_CONSTANTS
from registry.services.federation.base_client import BaseFederationClient
from registry_pkgs.models import A2AAgent, AgentCoreGateway, ExtendedMCPServer
from registry_pkgs.models.enums import AgentCoreTargetType, FederationSource

logger = logging.getLogger(__name__)


class AgentCoreFederationClient(BaseFederationClient):
    """
    AgentCore federation client.

    Public methods expose discovery operations used by import services.
    Private methods encapsulate AWS API calls, classification, and model transformation.
    """

    def __init__(
        self,
        region: str | None = None,
        timeout_seconds: int = 30,
        retry_attempts: int = 3,
    ):
        super().__init__("https://bedrock-agentcore-control.amazonaws.com", timeout_seconds, retry_attempts)
        self.region = (
            region or getattr(REGISTRY_CONSTANTS, "AWS_REGION", None) or os.getenv("AWS_REGION") or "us-east-1"
        )
        self._control_clients: dict[str, Any] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}

    async def discover_gateways(self, gateway_arns: list[str] | None = None) -> list[AgentCoreGateway]:
        """
        List AgentCore gateways in a region.

        Args:
            region is from client initialization.
            gateway_arns: Optional allow-list. If provided, the method validates all
                requested ARNs are visible and returns them in the same order.
        """
        control_client = await self._get_control_client()

        try:
            summaries = await asyncio.to_thread(self._list_gateways, control_client)
            details = await asyncio.to_thread(self._get_gateway_details, control_client, summaries)
        except Exception as exc:
            logger.error(f"Failed to list AgentCore gateways in {self.region}: {exc}", exc_info=True)
            return []

        discovered = [item for item in (self._normalize_gateway_summary(d, self.region) for d in details) if item]

        if gateway_arns:
            discovered_map = {item.arn: item for item in discovered if item.arn}
            missing = sorted(set(gateway_arns) - set(discovered_map))
            if missing:
                raise ValueError(f"Requested AgentCore gateways were not found or not accessible: {missing}")
            discovered = [discovered_map[arn] for arn in gateway_arns]

        return discovered

    async def discover_servers_from_gateway(
        self,
        gateway_arn: str,
        author_id: PydanticObjectId | None = None,
    ) -> list[ExtendedMCPServer]:
        """
        Discover gateway targets and convert each target into an ExtendedMCPServer.

        Notes:
        - Target details are fetched via GetGatewayTarget, because ListGatewayTargets only
          returns summary fields.
        - Multiple target backing types are supported (mcpServer/lambda/apiGateway/openApi/smithy).
        """
        region = self._extract_region_from_arn(gateway_arn)
        control_client = await self._get_control_client(region)

        try:
            gateway_data, target_summaries = await asyncio.to_thread(
                self._fetch_gateway_and_target_summaries,
                control_client,
                gateway_arn,
            )
        except Exception as exc:
            logger.error(f"Failed to fetch AgentCore gateway details for {gateway_arn}: {exc}", exc_info=True)
            return []

        gateway = AgentCoreGateway.model_construct(
            arn=gateway_data.get("gatewayArn", gateway_arn),
            name=gateway_data.get("name", self._extract_gateway_id(gateway_arn)),
            region=self._extract_region_from_arn(gateway_data.get("gatewayArn", gateway_arn), region),
            gatewayId=gateway_data.get("gatewayId", self._extract_gateway_id(gateway_arn)),
            gatewayUrl=gateway_data.get("gatewayUrl"),
        )
        try:
            target_details = await asyncio.to_thread(
                self._get_gateway_target_details,
                control_client,
                gateway_arn,
                target_summaries,
            )
        except Exception as exc:
            logger.error(f"Failed to fetch AgentCore targets for {gateway_arn}: {exc}", exc_info=True)
            return []
        return [self._transform_gateway_target_to_mcp_server(t, gateway, author_id) for t in target_details]

    async def discover_runtime_entities(
        self,
        runtime_arns: list[str] | None = None,
        author_id: PydanticObjectId | None = None,
    ) -> dict[str, list[Any]]:
        """
        Discover runtime details and classify by protocol.

        Mapping rules:
        - A2A runtime -> A2AAgent
        - MCP runtime -> ExtendedMCPServer
        - HTTP/unknown runtime -> skipped_runtimes

        Reconciliation rule:
        - If a runtime flips protocol between syncs, stale documents from the other
          model collection are deleted by federationId.
        """
        region = self.region
        control_client = await self._get_control_client(region)

        try:
            runtime_summaries = await asyncio.to_thread(self._list_runtime_summaries, control_client)
        except Exception as exc:
            logger.error(f"Failed to list AgentCore runtimes in {self.region}: {exc}", exc_info=True)
            return {"a2a_agents": [], "mcp_servers": [], "skipped_runtimes": []}

        summary_by_arn = {s["agentRuntimeArn"]: s for s in runtime_summaries if s.get("agentRuntimeArn")}
        selected_arns = runtime_arns or list(summary_by_arn.keys())

        selected_summaries: list[dict[str, Any]] = []
        for runtime_arn in selected_arns:
            summary = summary_by_arn.get(runtime_arn)
            if not summary:
                logger.warning(f"Runtime ARN not found in list_agent_runtimes: {runtime_arn}")
                continue
            selected_summaries.append(summary)

        runtime_details = await asyncio.to_thread(self._get_runtime_details, control_client, selected_summaries)

        a2a_agents: list[A2AAgent] = []
        mcp_servers: list[ExtendedMCPServer] = []
        skipped_runtimes: list[dict[str, Any]] = []

        for runtime_detail in runtime_details:
            protocol = self._extract_runtime_protocol(runtime_detail)
            runtime_arn = runtime_detail.get("agentRuntimeArn", "")

            if protocol == "A2A":
                await self._reconcile_runtime_type(runtime_arn=runtime_arn, target_type="a2a")
                a2a_agents.append(self._transform_runtime_to_a2a_agent(runtime_detail, region, author_id))
                continue

            if protocol == "MCP":
                await self._reconcile_runtime_type(runtime_arn=runtime_arn, target_type="mcp")
                mcp_servers.append(self._transform_runtime_to_mcp_server(runtime_detail, region, author_id))
                continue

            skipped_runtimes.append(
                {
                    "runtimeArn": runtime_arn,
                    "runtimeId": runtime_detail.get("agentRuntimeId"),
                    "runtimeName": runtime_detail.get("agentRuntimeName"),
                    "serverProtocol": protocol or "UNKNOWN",
                }
            )

        return {
            "a2a_agents": a2a_agents,
            "mcp_servers": mcp_servers,
            "skipped_runtimes": skipped_runtimes,
        }

    def fetch_server(self, server_name: str, **kwargs) -> dict[str, Any] | None:
        """
        BaseFederationClient compatibility wrapper.

        server_name is interpreted as gateway ARN.
        """
        author_id = kwargs.get("author_id")
        servers = self._run_async(self.discover_servers_from_gateway(server_name, author_id=author_id))
        if not servers:
            return None
        return self._server_to_dict(servers[0])

    def fetch_all_servers(self, server_names: list[str], **kwargs) -> list[dict[str, Any]]:
        """
        BaseFederationClient compatibility wrapper.

        server_names is interpreted as a list of gateway ARNs.
        """
        author_id = kwargs.get("author_id")
        servers: list[dict[str, Any]] = []
        for gateway_arn in server_names:
            discovered = self._run_async(self.discover_servers_from_gateway(gateway_arn, author_id=author_id))
            servers.extend(self._server_to_dict(server) for server in discovered)
        return servers

    def _server_to_dict(self, server: ExtendedMCPServer) -> dict[str, Any]:
        """Convert model document to BaseFederationClient-compatible dict."""
        if hasattr(server, "model_dump"):
            return server.model_dump(by_alias=True, exclude_none=True)
        return dict(server)

    def _init_boto3_client(self, region: str):
        """
        Initialize and cache the AgentCore control-plane boto3 client.

        Credential priority:
        1) explicit env keys
        2) default AWS credential chain
        3) optional STS assume role overlay
        """
        if region in self._control_clients:
            return self._control_clients[region]

        access_key = getattr(REGISTRY_CONSTANTS, "AWS_ACCESS_KEY_ID", None) or os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = getattr(REGISTRY_CONSTANTS, "AWS_SECRET_ACCESS_KEY", None) or os.getenv("AWS_SECRET_ACCESS_KEY")
        session_token = getattr(REGISTRY_CONSTANTS, "AWS_SESSION_TOKEN", None) or os.getenv("AWS_SESSION_TOKEN")
        assume_role_arn = getattr(REGISTRY_CONSTANTS, "AGENTCORE_ASSUME_ROLE_ARN", None) or os.getenv(
            "AGENTCORE_ASSUME_ROLE_ARN"
        )

        if access_key and secret_key:
            base_session = boto3.Session(
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=session_token,
            )
            logger.info("Initialized AgentCore AWS session with explicit access keys")
        else:
            base_session = boto3.Session(region_name=region)
            logger.info("Initialized AgentCore AWS session with default credential chain")

        session = base_session
        if assume_role_arn:
            sts_client = base_session.client("sts")
            assumed_role = sts_client.assume_role(
                RoleArn=assume_role_arn,
                RoleSessionName=f"agentcore-federation-{region}",
            )
            credentials = assumed_role["Credentials"]
            session = boto3.Session(
                region_name=region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            )
            logger.info("Initialized AgentCore AWS session via assume role")

        client = session.client("bedrock-agentcore-control", region_name=region)
        self._control_clients[region] = client
        return client

    async def _get_control_client(self, region: str) -> Any:
        """
        Async-safe client getter.

        boto3/session initialization may trigger network I/O (for example STS assume_role),
        so client initialization is executed in a worker thread to avoid blocking the event loop.
        A per-region lock prevents duplicate concurrent initialization.
        """
        cached = self._control_clients.get(region)
        if cached:
            return cached

        lock = self._client_locks.setdefault(region, asyncio.Lock())
        async with lock:
            cached = self._control_clients.get(region)
            if cached:
                return cached
            return await asyncio.to_thread(self._init_boto3_client, region)

    def _list_gateways(self, control_client: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"maxResults": 100}
            if next_token:
                kwargs["nextToken"] = next_token
            response = control_client.list_gateways(**kwargs)
            items.extend(response.get("items", []))
            next_token = response.get("nextToken")
            if not next_token:
                break
        return items

    def _get_gateway_details(self, control_client: Any, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        for summary in summaries:
            gateway_identifier = summary.get("gatewayArn") or summary.get("arn") or summary.get("gatewayId")
            if not gateway_identifier:
                logger.warning("Skipping gateway detail fetch due to missing identifier")
                continue
            try:
                detail_response = control_client.get_gateway(gatewayIdentifier=gateway_identifier)
                detail = detail_response.get("gateway", detail_response)
                details.append({**summary, **detail})
            except Exception as exc:
                logger.error(
                    f"Failed to fetch AgentCore gateway detail for identifier={gateway_identifier}: {exc}",
                    exc_info=True,
                )
                details.append(summary)
        return details

    def _fetch_gateway_and_target_summaries(
        self,
        control_client: Any,
        gateway_arn: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        gateway_response = control_client.get_gateway(gatewayIdentifier=gateway_arn)
        gateway_data = gateway_response.get("gateway", gateway_response)

        target_items: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"gatewayIdentifier": gateway_arn, "maxResults": 100}
            if next_token:
                kwargs["nextToken"] = next_token
            targets_response = control_client.list_gateway_targets(**kwargs)
            target_items.extend(targets_response.get("items", []))
            next_token = targets_response.get("nextToken")
            if not next_token:
                break
        return gateway_data, target_items

    def _get_gateway_target_details(
        self,
        control_client: Any,
        gateway_arn: str,
        target_summaries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        for summary in target_summaries:
            target_id = summary.get("targetId")
            if not target_id:
                logger.warning("Skipping gateway target detail fetch due to missing targetId")
                continue
            try:
                detail = control_client.get_gateway_target(gatewayIdentifier=gateway_arn, targetId=target_id)
                details.append({**summary, **detail})
            except Exception as exc:
                logger.error(
                    f"Failed to fetch AgentCore gateway target detail for targetId={target_id}: {exc}",
                    exc_info=True,
                )
        return details

    def _list_runtime_summaries(self, control_client: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"maxResults": 100}
            if next_token:
                kwargs["nextToken"] = next_token
            response = control_client.list_agent_runtimes(**kwargs)
            items.extend(response.get("agentRuntimes", []))
            next_token = response.get("nextToken")
            if not next_token:
                break
        return items

    def _get_runtime_details(self, control_client: Any, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        for summary in summaries:
            runtime_id = summary.get("agentRuntimeId")
            runtime_version = summary.get("agentRuntimeVersion")
            if not runtime_id or not runtime_version:
                logger.warning(
                    "Skipping runtime detail fetch due to missing runtime id/version: "
                    f"id={runtime_id}, version={runtime_version}"
                )
                continue

            try:
                detail = control_client.get_agent_runtime(
                    agentRuntimeId=runtime_id,
                    agentRuntimeVersion=runtime_version,
                )
                details.append({**summary, **detail})
            except Exception as exc:
                logger.error(
                    f"Failed to fetch AgentCore runtime detail for id={runtime_id}, version={runtime_version}: {exc}",
                    exc_info=True,
                )
        return details

    def _transform_gateway_target_to_mcp_server(
        self,
        target_data: dict[str, Any],
        gateway: AgentCoreGateway,
        author_id: PydanticObjectId | None = None,
    ) -> ExtendedMCPServer:
        target_name = target_data.get("name", target_data.get("targetId", "unknown-target"))
        target_id = target_data.get("targetId", target_name)
        target_type = self._extract_target_type(target_data)
        target_endpoint = self._extract_target_endpoint(target_data, gateway)

        gateway_id = gateway.gatewayId or self._extract_gateway_id(gateway.arn)
        gateway_url = (gateway.gatewayUrl or self._build_gateway_url(gateway_id, gateway.region)).rstrip("/")
        if not gateway_url.endswith("/mcp"):
            gateway_url = f"{gateway_url}/mcp"

        server_info = {
            "server_name": target_name,
            "path": f"/agentcore/{self._slug(gateway.name)}/{self._slug(target_name)}",
            "tags": ["bedrock", "agentcore", "aws", "federated"],
            "config": {
                "title": target_data.get("description", target_name),
                "description": target_data.get("description", ""),
                "type": "streamable-http",
                "url": target_endpoint or f"{gateway_url}/",
                "requiresOAuth": True,
                "authProvider": "bedrock-agentcore",
            },
            "author": author_id or PydanticObjectId(),
            "federationSource": FederationSource.AGENTCORE,
            "federationId": target_id,
            "federationGatewayArn": gateway.arn,
            "federationSyncedAt": datetime.now(UTC),
            "federationMetadata": {
                "sourceType": "gateway_target",
                "gatewayId": gateway_id,
                "targetId": target_id,
                "targetStatus": target_data.get("status"),
                "targetType": target_type.value,
                "targetConfiguration": target_data.get("targetConfiguration"),
                "credentialProviderConfigured": bool(target_data.get("credentialProviderConfigurations")),
            },
        }
        return ExtendedMCPServer.from_server_info(server_info=server_info, is_enabled=True)

    def _transform_runtime_to_a2a_agent(
        self,
        runtime_detail: dict[str, Any],
        region: str,
        author_id: PydanticObjectId | None = None,
    ) -> A2AAgent:
        runtime_arn = runtime_detail.get("agentRuntimeArn", "")
        runtime_id = runtime_detail.get("agentRuntimeId")
        runtime_version = runtime_detail.get("agentRuntimeVersion")
        runtime_name = runtime_detail.get("agentRuntimeName", "agentcore-runtime")
        escaped_runtime_arn = quote(runtime_arn, safe="")
        runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations/"

        card_data = {
            "name": runtime_name,
            "description": runtime_detail.get("description", f"AgentCore runtime {runtime_name}"),
            "url": runtime_url,
            "version": str(runtime_version) if runtime_version else "1.0.0",
            "protocolVersion": "1.0",
            "capabilities": {"streaming": True},
            "skills": [],
            "securitySchemes": {},
            "preferredTransport": "HTTP+JSON",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["application/json"],
        }

        return A2AAgent.from_a2a_agent_card(
            card_data=card_data,
            path=f"/agentcore/a2a/{self._slug(runtime_name)}",
            author=author_id or PydanticObjectId(),
            isEnabled=True,
            status="active",
            tags=["agentcore", "a2a", "aws", "federated"],
            registeredBy="agentcore-federation",
            registeredAt=datetime.now(UTC),
            federationSource=FederationSource.AGENTCORE,
            federationId=runtime_arn,
            federationGatewayArn=None,
            federationSyncedAt=datetime.now(UTC),
            federationMetadata={
                "sourceType": "runtime",
                "runtimeArn": runtime_arn,
                "runtimeId": runtime_id,
                "runtimeVersion": runtime_version,
                "runtimeStatus": runtime_detail.get("status"),
                "lastUpdatedAt": runtime_detail.get("lastUpdatedAt"),
                "createdAt": runtime_detail.get("createdAt"),
                "failureReason": runtime_detail.get("failureReason"),
                "workloadIdentityDetails": runtime_detail.get("workloadIdentityDetails"),
                "protocolConfiguration": runtime_detail.get("protocolConfiguration"),
                "authorizerConfiguration": runtime_detail.get("authorizerConfiguration"),
            },
            wellKnown={
                "enabled": True,
                "url": f"{runtime_url}.well-known/agent-card.json",
                "lastSyncStatus": "success" if runtime_detail.get("status") == "READY" else "failed",
                "lastSyncVersion": str(runtime_version) if runtime_version else "1.0.0",
                "syncError": None,
                "lastSyncAt": datetime.now(UTC),
            },
        )

    def _transform_runtime_to_mcp_server(
        self,
        runtime_detail: dict[str, Any],
        region: str,
        author_id: PydanticObjectId | None = None,
    ) -> ExtendedMCPServer:
        runtime_arn = runtime_detail.get("agentRuntimeArn", "")
        runtime_id = runtime_detail.get("agentRuntimeId")
        runtime_name = runtime_detail.get("agentRuntimeName", "agentcore-runtime")
        runtime_version = runtime_detail.get("agentRuntimeVersion")
        escaped_runtime_arn = quote(runtime_arn, safe="")
        runtime_mcp_url = (
            f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations/mcp/"
        )

        server_info = {
            "server_name": runtime_name,
            "path": f"/agentcore/mcp/{self._slug(runtime_name)}",
            "tags": ["bedrock", "agentcore", "aws", "mcp-runtime", "federated"],
            "config": {
                "title": runtime_name,
                "description": runtime_detail.get("description", f"AgentCore MCP runtime {runtime_name}"),
                "type": "streamable-http",
                "url": runtime_mcp_url,
                "requiresOAuth": True,
                "authProvider": "bedrock-agentcore",
            },
            "author": author_id or PydanticObjectId(),
            "federationSource": FederationSource.AGENTCORE,
            "federationId": runtime_arn,
            "federationGatewayArn": None,
            "federationSyncedAt": datetime.now(UTC),
            "federationMetadata": {
                "sourceType": "runtime",
                "runtimeArn": runtime_arn,
                "runtimeId": runtime_id,
                "runtimeName": runtime_name,
                "runtimeVersion": runtime_version,
                "runtimeStatus": runtime_detail.get("status"),
                "serverProtocol": "MCP",
                "lastUpdatedAt": runtime_detail.get("lastUpdatedAt"),
                "createdAt": runtime_detail.get("createdAt"),
                "protocolConfiguration": runtime_detail.get("protocolConfiguration"),
            },
        }
        return ExtendedMCPServer.from_server_info(server_info=server_info, is_enabled=True)

    def _extract_runtime_protocol(self, runtime_detail: dict[str, Any]) -> str:
        config = runtime_detail.get("protocolConfiguration") or {}
        return str(config.get("serverProtocol", "")).upper()

    def _extract_target_type(self, target_data: dict[str, Any]) -> AgentCoreTargetType:
        """Infer gateway target type from GetGatewayTarget.targetConfiguration payload."""
        config = target_data.get("targetConfiguration", {})
        mcp_cfg = config.get("mcp", {})
        if mcp_cfg.get("mcpServer"):
            return AgentCoreTargetType.MCP_SERVER
        if mcp_cfg.get("lambda"):
            return AgentCoreTargetType.LAMBDA_ARN
        if mcp_cfg.get("apiGateway"):
            return AgentCoreTargetType.API_GATEWAY
        if mcp_cfg.get("openApiSchema"):
            return AgentCoreTargetType.REST_API
        if mcp_cfg.get("smithyModel"):
            return AgentCoreTargetType.INTEGRATIONS
        return AgentCoreTargetType.UNKNOWN

    def _extract_target_endpoint(self, target_data: dict[str, Any], gateway: AgentCoreGateway) -> str | None:
        """Best-effort endpoint extraction for heterogeneous gateway target types."""
        config = target_data.get("targetConfiguration", {})
        mcp_cfg = config.get("mcp", {})

        mcp_server_cfg = mcp_cfg.get("mcpServer", {})
        if mcp_server_cfg.get("endpoint"):
            return str(mcp_server_cfg["endpoint"])

        lambda_cfg = mcp_cfg.get("lambda", {})
        if lambda_cfg.get("lambdaArn"):
            return f"lambda://{lambda_cfg['lambdaArn']}"

        api_gateway_cfg = mcp_cfg.get("apiGateway", {})
        rest_api_id = api_gateway_cfg.get("restApiId")
        stage = api_gateway_cfg.get("stage")
        if rest_api_id and stage:
            return f"https://{rest_api_id}.execute-api.{gateway.region}.amazonaws.com/{stage}"

        if mcp_cfg.get("openApiSchema"):
            return "openapi://inline-or-s3-schema"
        if mcp_cfg.get("smithyModel"):
            return "smithy://inline-or-s3-model"
        return None

    async def _reconcile_runtime_type(self, runtime_arn: str, target_type: str) -> None:
        """
        Remove stale model documents if runtime protocol changed between syncs.

        target_type:
        - "a2a": keep A2AAgent, delete ExtendedMCPServer by federationId
        - "mcp": keep ExtendedMCPServer, delete A2AAgent by federationId
        """
        if not runtime_arn:
            return

        if target_type == "a2a":
            existing_mcp = await ExtendedMCPServer.find_one(
                {"federationSource": FederationSource.AGENTCORE, "federationId": runtime_arn}
            )
            if existing_mcp:
                logger.info(
                    f"Runtime type changed to A2A, deleting previous MCP server model for federationId={runtime_arn}"
                )
                await existing_mcp.delete()
            return

        if target_type == "mcp":
            existing_a2a = await A2AAgent.find_one(
                {"federationSource": FederationSource.AGENTCORE, "federationId": runtime_arn}
            )
            if existing_a2a:
                logger.info(
                    f"Runtime type changed to MCP, deleting previous A2A agent model for federationId={runtime_arn}"
                )
                await existing_a2a.delete()

    def _normalize_gateway_summary(self, summary: dict[str, Any], default_region: str) -> AgentCoreGateway | None:
        arn = summary.get("gatewayArn") or summary.get("arn")

        gateway_id = summary.get("gatewayId") or (self._extract_gateway_id(arn) if arn else None)
        if not gateway_id:
            return None

        if not arn:
            # Fallback canonical placeholder when ListGateways summary does not include ARN.
            arn = f"gateway/{gateway_id}"

        region = self._extract_region_from_arn(arn, default_region)
        return AgentCoreGateway.model_construct(
            arn=arn,
            name=summary.get("name", gateway_id),
            region=region,
            gatewayId=gateway_id,
            gatewayUrl=summary.get("gatewayUrl") or self._build_gateway_url(gateway_id, region),
        )

    def _build_gateway_url(self, gateway_id: str, region: str) -> str:
        return f"https://{gateway_id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"

    def _extract_gateway_id(self, gateway_arn: str) -> str:
        resource = gateway_arn.split(":", 5)[-1]
        return resource.split("/", 1)[1] if "/" in resource else resource

    def _extract_region_from_arn(self, arn: str, fallback: str = "us-east-1") -> str:
        parts = arn.split(":")
        return parts[3] if len(parts) > 3 and parts[3] else fallback

    def _slug(self, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "-").replace("_", "-")
        return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-/")

    def _run_async(self, coroutine: Any) -> Any:
        """Run async coroutine from sync wrapper methods."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        raise RuntimeError(
            "AgentCoreFederationClient synchronous fetch methods cannot be called from an active event loop. "
            "Use async discovery methods instead."
        )
