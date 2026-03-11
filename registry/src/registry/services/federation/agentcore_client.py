import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import boto3
from beanie import PydanticObjectId

from registry.constants import REGISTRY_CONSTANTS
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.enums import FederationSource

logger = logging.getLogger(__name__)


class AgentCoreFederationClient:
    """
    Runtime-only AgentCore federation client.

    This client discovers AgentCore runtimes and transforms them into:
    - A2AAgent (for A2A runtimes)
    - ExtendedMCPServer (for MCP runtimes)
    """

    def __init__(
        self,
        region: str | None = None,
    ):
        self.region = region or REGISTRY_CONSTANTS.AWS_REGION or "us-east-1"
        self._control_clients: dict[str, Any] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}

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
        - HTTP/AGUI/unknown runtime -> skipped_runtimes
        """
        control_client = await self._get_control_client(self.region)

        try:
            runtime_summaries = await asyncio.to_thread(self._list_runtime_summaries, control_client)
        except Exception as exc:
            logger.error("Failed to list AgentCore runtimes in %s: %s", self.region, exc, exc_info=True)
            return {"a2a_agents": [], "mcp_servers": [], "skipped_runtimes": []}

        summary_by_arn = {s["agentRuntimeArn"]: s for s in runtime_summaries if "agentRuntimeArn" in s}
        selected_arns = runtime_arns or list(summary_by_arn.keys())

        selected_summaries: list[dict[str, Any]] = []
        for runtime_arn in selected_arns:
            summary = summary_by_arn.get(runtime_arn)
            if not summary:
                logger.warning("Runtime ARN not found in list_agent_runtimes: %s", runtime_arn)
                continue
            selected_summaries.append(summary)

        runtime_details = await asyncio.to_thread(self._get_runtime_details, control_client, selected_summaries)

        a2a_agents: list[A2AAgent] = []
        mcp_servers: list[ExtendedMCPServer] = []
        skipped_runtimes: list[dict[str, Any]] = []

        for runtime_detail in runtime_details:
            runtime_arn = runtime_detail["agentRuntimeArn"]
            runtime_id = runtime_detail["agentRuntimeId"]
            runtime_name = runtime_detail["agentRuntimeName"]
            protocol = self._extract_runtime_protocol(runtime_detail)

            if protocol == "A2A":
                await self._reconcile_runtime_type(runtime_arn=runtime_arn, target_type="a2a")
                a2a_agents.append(self._transform_runtime_to_a2a_agent(runtime_detail, self.region, author_id))
                continue

            if protocol == "MCP":
                await self._reconcile_runtime_type(runtime_arn=runtime_arn, target_type="mcp")
                mcp_servers.append(self._transform_runtime_to_mcp_server(runtime_detail, self.region, author_id))
                continue

            skipped_runtimes.append(
                {
                    "runtimeArn": runtime_arn,
                    "runtimeId": runtime_id,
                    "runtimeName": runtime_name,
                    "serverProtocol": protocol or "UNKNOWN",
                }
            )

        return {
            "a2a_agents": a2a_agents,
            "mcp_servers": mcp_servers,
            "skipped_runtimes": skipped_runtimes,
        }

    def _init_boto3_client(self, region: str):
        if region in self._control_clients:
            return self._control_clients[region]

        access_key = REGISTRY_CONSTANTS.AWS_ACCESS_KEY_ID
        secret_key = REGISTRY_CONSTANTS.AWS_SECRET_ACCESS_KEY
        session_token = REGISTRY_CONSTANTS.AWS_SESSION_TOKEN
        assume_role_arn = REGISTRY_CONSTANTS.AGENTCORE_ASSUME_ROLE_ARN

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
        cached = self._control_clients.get(region)
        if cached:
            return cached

        lock = self._client_locks.setdefault(region, asyncio.Lock())
        async with lock:
            cached = self._control_clients.get(region)
            if cached:
                return cached
            return await asyncio.to_thread(self._init_boto3_client, region)

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
            runtime_id = summary["agentRuntimeId"]
            runtime_version = summary["agentRuntimeVersion"]
            detail = control_client.get_agent_runtime(
                agentRuntimeId=runtime_id,
                agentRuntimeVersion=runtime_version,
            )
            details.append({**summary, **detail})
        return details

    def _transform_runtime_to_a2a_agent(
        self,
        runtime_detail: dict[str, Any],
        region: str,
        author_id: PydanticObjectId | None = None,
    ) -> A2AAgent:
        runtime_arn = runtime_detail["agentRuntimeArn"]
        runtime_id = runtime_detail["agentRuntimeId"]
        runtime_version = runtime_detail["agentRuntimeVersion"]
        runtime_name = runtime_detail["agentRuntimeName"]
        runtime_base_url = self._build_runtime_invocation_url(runtime_arn=runtime_arn, region=region)

        card_data = {
            "name": runtime_name,
            "description": runtime_detail.get("description", f"AgentCore runtime {runtime_name}"),
            "url": runtime_base_url,
            "version": str(runtime_version),
            "protocolVersion": "1.0",
            "capabilities": {"streaming": True},
            "skills": [],
            "securitySchemes": {},
            "preferredTransport": "HTTP+JSON",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["application/json"],
        }

        status = runtime_detail.get("status", "READY")
        return A2AAgent.from_a2a_agent_card(
            card_data=card_data,
            path=f"/agentcore/a2a/{self._slug(runtime_name)}",
            author=author_id or PydanticObjectId(),
            isEnabled=status == "READY",
            status="active" if status == "READY" else "inactive",
            tags=["agentcore", "a2a", "aws", "federated"],
            registeredBy="agentcore-federation",
            registeredAt=datetime.now(UTC),
            federationSource=FederationSource.AGENTCORE,
            federationId=runtime_arn,
            federationSyncedAt=datetime.now(UTC),
            federationMetadata={
                "sourceType": "runtime",
                "runtimeArn": runtime_arn,
                "runtimeId": runtime_id,
                "runtimeVersion": runtime_version,
                "runtimeStatus": status,
                "lastUpdatedAt": runtime_detail.get("lastUpdatedAt"),
                "createdAt": runtime_detail.get("createdAt"),
                "failureReason": runtime_detail.get("failureReason"),
                "workloadIdentityDetails": runtime_detail.get("workloadIdentityDetails"),
                "protocolConfiguration": runtime_detail.get("protocolConfiguration"),
                "authorizerConfiguration": runtime_detail.get("authorizerConfiguration"),
            },
            wellKnown={
                "enabled": True,
                "url": f"{runtime_base_url}/.well-known/agent-card.json?qualifier=DEFAULT",
                "lastSyncStatus": "success" if status == "READY" else "failed",
                "lastSyncVersion": str(runtime_version),
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
        runtime_arn = runtime_detail["agentRuntimeArn"]
        runtime_id = runtime_detail["agentRuntimeId"]
        runtime_name = runtime_detail["agentRuntimeName"]
        runtime_version = runtime_detail["agentRuntimeVersion"]
        runtime_mcp_url = (
            f"{self._build_runtime_invocation_url(runtime_arn=runtime_arn, region=region)}?qualifier=DEFAULT"
        )
        status = runtime_detail.get("status", "READY")

        server_info = {
            "server_name": runtime_name,
            "path": f"/agentcore/mcp/{self._slug(runtime_name)}",
            "tags": ["bedrock", "agentcore", "aws", "mcp-runtime", "federated"],
            "config": {
                "title": runtime_name,
                "description": runtime_detail.get("description", f"AgentCore MCP runtime {runtime_name}"),
                "type": "streamable-http",
                "url": runtime_mcp_url,
                "requiresOAuth": False,
                "authProvider": "bedrock-agentcore",
            },
            "author": author_id or PydanticObjectId(),
            "federationSource": FederationSource.AGENTCORE,
            "federationId": runtime_arn,
            "federationSyncedAt": datetime.now(UTC),
            "federationMetadata": {
                "sourceType": "runtime",
                "runtimeArn": runtime_arn,
                "runtimeId": runtime_id,
                "runtimeName": runtime_name,
                "runtimeVersion": runtime_version,
                "runtimeStatus": status,
                "serverProtocol": "MCP",
                "lastUpdatedAt": runtime_detail.get("lastUpdatedAt"),
                "createdAt": runtime_detail.get("createdAt"),
                "protocolConfiguration": runtime_detail.get("protocolConfiguration"),
            },
        }
        return ExtendedMCPServer.from_server_info(server_info=server_info, is_enabled=status == "READY")

    def _extract_runtime_protocol(self, runtime_detail: dict[str, Any]) -> str:
        config = runtime_detail.get("protocolConfiguration") or {}
        return str(config.get("serverProtocol", "")).upper()

    async def _reconcile_runtime_type(self, runtime_arn: str, target_type: str) -> None:
        if target_type == "a2a":
            existing_mcp = await ExtendedMCPServer.find_one(
                {"federationSource": FederationSource.AGENTCORE, "federationId": runtime_arn}
            )
            if existing_mcp:
                logger.info(
                    "Runtime type changed to A2A, deleting previous MCP server model for federationId=%s",
                    runtime_arn,
                )
                await existing_mcp.delete()
            return

        if target_type == "mcp":
            existing_a2a = await A2AAgent.find_one(
                {"federationSource": FederationSource.AGENTCORE, "federationId": runtime_arn}
            )
            if existing_a2a:
                logger.info(
                    "Runtime type changed to MCP, deleting previous A2A agent model for federationId=%s",
                    runtime_arn,
                )
                await existing_a2a.delete()

    def _extract_region_from_arn(self, arn: str, fallback: str = "us-east-1") -> str:
        parts = arn.split(":")
        return parts[3] if len(parts) > 3 and parts[3] else fallback

    def _slug(self, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "-").replace("_", "-")
        return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-/")

    def _build_runtime_invocation_url(self, runtime_arn: str, region: str) -> str:
        escaped_runtime_arn = quote(runtime_arn, safe="")
        return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations"
