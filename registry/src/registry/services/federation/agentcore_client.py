import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from beanie import PydanticObjectId

from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.enums import FederationSource

from ...constants import REGISTRY_CONSTANTS
from .agentcore_client_provider import AgentCoreClientProvider
from .runtime_invoker import AgentCoreRuntimeInvoker

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
        client_provider: AgentCoreClientProvider | None = None,
        runtime_invoker: AgentCoreRuntimeInvoker | None = None,
    ):
        self.region = region or REGISTRY_CONSTANTS.AWS_REGION or "us-east-1"
        self.client_provider = client_provider or AgentCoreClientProvider(default_region=self.region)
        self.runtime_invoker = runtime_invoker or AgentCoreRuntimeInvoker(
            default_region=self.region,
            get_runtime_client=self.client_provider.get_runtime_client,
            get_runtime_credentials_provider=self.client_provider.get_runtime_credentials_provider,
            extract_region_from_arn=self._extract_region_from_arn,
        )

    async def discover_runtime_entities(
        self,
        runtime_arns: list[str] | None = None,
        author_id: PydanticObjectId | None = None,
        enrich_protocol_payloads: bool = True,
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
                a2a_agent = self._transform_runtime_to_a2a_agent(runtime_detail, self.region, author_id)
                if enrich_protocol_payloads:
                    await self._enrich_a2a_agent(a2a_agent, runtime_detail, self.region)
                a2a_agents.append(a2a_agent)
                continue

            if protocol == "MCP":
                await self._reconcile_runtime_type(runtime_arn=runtime_arn, target_type="mcp")
                mcp_server = self._transform_runtime_to_mcp_server(runtime_detail, self.region, author_id)
                if enrich_protocol_payloads:
                    await self._enrich_mcp_server(mcp_server)
                mcp_servers.append(mcp_server)
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

    async def invoke_runtime_prompt(
        self,
        runtime_arn: str,
        prompt: str = "ping",
        qualifier: str = "DEFAULT",
    ) -> dict[str, Any]:
        return await self.runtime_invoker.invoke_runtime_prompt(
            runtime_arn=runtime_arn,
            prompt=prompt,
            qualifier=qualifier,
        )

    async def _get_control_client(self, region: str) -> Any:
        return await self.client_provider.get_control_client(region)

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

    async def _enrich_mcp_server(self, server: ExtendedMCPServer) -> None:
        config = server.config or {}
        runtime_url = config.get("url")
        if not runtime_url:
            return

        try:
            result = await self.runtime_invoker.fetch_mcp_payloads(
                runtime_url=runtime_url,
                transport_type=config.get("type"),
                metadata=server.federationMetadata or {},
                runtime_detail=server.federationMetadata or {},
            )
        except Exception as exc:
            logger.warning("MCP runtime enrichment failed for %s: %s", server.serverName, exc)
            metadata = dict(server.federationMetadata or {})
            metadata["enrichmentError"] = f"mcp enrichment failed: {exc}"
            metadata["enrichedAt"] = datetime.now(UTC)
            server.federationMetadata = metadata
            return

        if result.error_message:
            logger.warning("MCP runtime enrichment returned error for %s: %s", server.serverName, result.error_message)
            metadata = dict(server.federationMetadata or {})
            metadata["enrichedAt"] = datetime.now(UTC)
            metadata["enrichmentError"] = result.error_message
            server.federationMetadata = metadata
            return

        tools = result.tools or []
        resources = result.resources or []
        prompts = result.prompts or []
        capabilities = result.capabilities or {}

        config["toolFunctions"] = self._convert_tools_to_tool_functions(tools, server.serverName)
        config["tools"] = ", ".join([tool.get("name", "") for tool in tools if tool.get("name")])
        config["resources"] = resources
        config["prompts"] = prompts
        config["capabilities"] = json.dumps(capabilities, ensure_ascii=False) if capabilities else "{}"
        config["requiresInit"] = bool(result.requires_init) if result.requires_init is not None else False
        server.config = config
        server.numTools = len(tools)

        metadata = dict(server.federationMetadata or {})
        metadata["enrichedAt"] = datetime.now(UTC)
        metadata["enrichmentError"] = result.error_message
        server.federationMetadata = metadata

    async def _enrich_a2a_agent(self, agent: A2AAgent, runtime_detail: dict[str, Any], region: str) -> None:
        runtime_arn = runtime_detail["agentRuntimeArn"]
        escaped_runtime_arn = quote(runtime_arn, safe="")
        card_url = (
            f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations"
            f"/.well-known/agent-card.json?qualifier=DEFAULT"
        )

        try:
            card_data = await self.runtime_invoker.fetch_a2a_card(
                card_url=card_url,
                metadata=agent.federationMetadata or {},
                runtime_detail=runtime_detail,
            )
        except Exception as exc:
            logger.warning("A2A runtime enrichment failed for %s: %s", agent.card.name, exc)
            if agent.wellKnown:
                agent.wellKnown.lastSyncStatus = "failed"
                agent.wellKnown.syncError = str(exc)
                agent.wellKnown.lastSyncAt = datetime.now(UTC)
            metadata = dict(agent.federationMetadata or {})
            metadata["enrichedAt"] = datetime.now(UTC)
            metadata["enrichmentError"] = f"a2a enrichment failed: {exc}"
            agent.federationMetadata = metadata
            return

        card_payload = self._extract_a2a_card_payload(card_data)
        fallback_card = agent.card.model_dump(mode="json")
        merged = {**fallback_card, **card_payload}
        merged["url"] = fallback_card.get("url")

        refreshed = A2AAgent.from_a2a_agent_card(
            card_data=merged,
            path=agent.path,
            author=agent.author,
            isEnabled=agent.isEnabled,
            status=agent.status,
            tags=agent.tags,
            registeredBy=agent.registeredBy,
            registeredAt=agent.registeredAt,
            federationSource=agent.federationSource,
            federationId=agent.federationId,
            federationSyncedAt=agent.federationSyncedAt or datetime.now(UTC),
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
        metadata["enrichedAt"] = datetime.now(UTC)
        metadata["enrichmentError"] = None
        agent.federationMetadata = metadata

    @staticmethod
    def _extract_a2a_card_payload(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        if isinstance(payload.get("agentCard"), dict):
            return payload["agentCard"]
        if isinstance(payload.get("card"), dict):
            return payload["card"]
        return payload

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
                "authorizerConfiguration": runtime_detail.get("authorizerConfiguration"),
            },
        }
        return ExtendedMCPServer.from_server_info(server_info=server_info, is_enabled=status == "READY")

    def _runtime_requires_oauth(self, runtime_detail: dict[str, Any]) -> bool:
        mode = self._detect_runtime_auth_mode(metadata=runtime_detail, runtime_detail=runtime_detail)
        return mode == "JWT"

    def _detect_runtime_auth_mode(
        self,
        metadata: dict[str, Any],
        runtime_detail: dict[str, Any] | None = None,
    ) -> str:
        return self.runtime_invoker.detect_runtime_auth_mode(metadata=metadata, runtime_detail=runtime_detail)

    @staticmethod
    def _map_agentcore_status_to_registry_status(agentcore_status: str | None) -> str:
        status = (agentcore_status or "").upper()
        if status == "READY":
            return "active"
        if status in {"FAILED", "ERROR"}:
            return "error"
        return "inactive"

    def _extract_runtime_protocol(self, runtime_detail: dict[str, Any]) -> str:
        config = runtime_detail.get("protocolConfiguration") or {}
        return str(config.get("serverProtocol", "")).upper()

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

    @staticmethod
    def _extract_region_from_arn(arn: str, fallback: str = "us-east-1") -> str:
        parts = arn.split(":")
        return parts[3] if len(parts) > 3 and parts[3] else fallback

    @staticmethod
    def _slug(value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "-").replace("_", "-")
        return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-/")

    @staticmethod
    def _build_runtime_invocation_url(runtime_arn: str, region: str) -> str:
        escaped_runtime_arn = quote(runtime_arn, safe="")
        return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations"
