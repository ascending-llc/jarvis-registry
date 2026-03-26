import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import boto3
from beanie import PydanticObjectId

from registry.core.config import settings
from registry_pkgs.models import A2AAgent, ExtendedMCPServer

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
        self.region = region or settings.aws_region or "us-east-1"
        self._control_clients: dict[str, Any] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}
        self.client_provider = client_provider or AgentCoreClientProvider(default_region=self.region)
        self.runtime_invoker = runtime_invoker or AgentCoreRuntimeInvoker(
            default_region=self.region,
            get_runtime_client=self.client_provider.get_runtime_client,
            get_runtime_credentials_provider=self.client_provider.get_runtime_credentials_provider,
            extract_region_from_arn=self.extract_region_from_arn,
        )

    async def discover_runtime_entities(
        self,
        runtime_arns: list[str] | None = None,
        author_id: PydanticObjectId | None = None,
        enrich_protocol_payloads: bool = True,
        region: str | None = None,
        resource_tags_filter: dict[str, str] | None = None,
    ) -> dict[str, list[Any]]:
        """
        Discover runtime details and classify by protocol.

        Mapping rules:
        - A2A runtime -> A2AAgent
        - MCP runtime -> ExtendedMCPServer
        - HTTP/AGUI/unknown runtime -> skipped_runtimes

        Discovery scope:
        - If runtime_arns is provided, only those runtimes are resolved.
        - Otherwise, the client lists every AgentCore runtime in the selected region.
        - This is a single-region scan, not a multi-region crawl.
        - If resource_tags_filter is provided, only runtimes whose AWS resource
          tags fully match the filter are imported.
        """
        selected_region = region or self.region
        control_client = await self._get_control_client(selected_region)
        normalized_tag_filter = dict(resource_tags_filter or {})

        try:
            runtime_summaries = await asyncio.to_thread(self._list_runtime_summaries, control_client)
        except Exception as exc:
            logger.error("Failed to list AgentCore runtimes in %s: %s", selected_region, exc, exc_info=True)
            raise RuntimeError(f"Failed to list AgentCore runtimes in {selected_region}: {exc}") from exc

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
        total_candidates = len(runtime_details)
        filtered_out_count = 0

        if normalized_tag_filter:
            logger.info(
                "Applying AgentCore runtime tag filter in region %s: filter=%s total_candidates=%d",
                selected_region,
                normalized_tag_filter,
                total_candidates,
            )
            runtime_details, filtered_runtimes = await asyncio.to_thread(
                self._filter_runtime_details_by_tags,
                control_client,
                runtime_details,
                normalized_tag_filter,
            )
            filtered_out_count = len(filtered_runtimes)
        else:
            filtered_runtimes = []

        logger.info(
            "AgentCore discovery candidates in region %s: total=%d matched_after_tag_filter=%d filtered_out=%d",
            selected_region,
            total_candidates,
            len(runtime_details),
            filtered_out_count,
        )

        a2a_agents: list[A2AAgent] = []
        mcp_servers: list[ExtendedMCPServer] = []
        skipped_runtimes: list[dict[str, Any]] = list(filtered_runtimes)

        for runtime_detail in runtime_details:
            runtime_arn = runtime_detail["agentRuntimeArn"]
            runtime_id = runtime_detail["agentRuntimeId"]
            runtime_name = runtime_detail["agentRuntimeName"]
            protocol = self._extract_runtime_protocol(runtime_detail)

            if protocol == "A2A":
                await self._reconcile_runtime_type(runtime_arn=runtime_arn, target_type="a2a")
                a2a_agent = self._transform_runtime_to_a2a_agent(runtime_detail, selected_region, author_id)
                if enrich_protocol_payloads:
                    await self._enrich_a2a_agent(a2a_agent, runtime_detail, selected_region)
                a2a_agents.append(a2a_agent)
                continue

            if protocol == "MCP":
                await self._reconcile_runtime_type(runtime_arn=runtime_arn, target_type="mcp")
                mcp_server = self._transform_runtime_to_mcp_server(runtime_detail, selected_region, author_id)
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

        logger.info(
            "AgentCore discovery completed in region %s: matched_runtimes=%d mcp_servers=%d a2a_agents=%d skipped=%d",
            selected_region,
            len(runtime_details),
            len(mcp_servers),
            len(a2a_agents),
            len(skipped_runtimes),
        )
        return {
            "a2a_agents": a2a_agents,
            "mcp_servers": mcp_servers,
            "skipped_runtimes": skipped_runtimes,
        }

    def _init_boto3_client(self, region: str):
        if region in self._control_clients:
            return self._control_clients[region]

        access_key = settings.aws_access_key_id
        secret_key = settings.aws_secret_access_key
        session_token = settings.aws_session_token

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

        client = base_session.client("bedrock-agentcore-control", region_name=region)
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

    def _list_runtime_tags(self, control_client: Any, runtime_arn: str) -> dict[str, str]:
        response = control_client.list_tags_for_resource(resourceArn=runtime_arn)
        return dict(response.get("tags", {}) or {})

    @staticmethod
    def _matches_resource_tags(runtime_tags: dict[str, str], required_tags: dict[str, str]) -> bool:
        return all(str(runtime_tags.get(key)) == str(expected) for key, expected in required_tags.items())

    def _filter_runtime_details_by_tags(
        self,
        control_client: Any,
        runtime_details: list[dict[str, Any]],
        required_tags: dict[str, str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        matched_details: list[dict[str, Any]] = []
        filtered_runtimes: list[dict[str, Any]] = []

        for runtime_detail in runtime_details:
            runtime_arn = runtime_detail["agentRuntimeArn"]
            runtime_name = runtime_detail.get("agentRuntimeName")
            runtime_tags = self._list_runtime_tags(control_client, runtime_arn)
            runtime_detail["tags"] = runtime_tags

            if self._matches_resource_tags(runtime_tags, required_tags):
                matched_details.append(runtime_detail)
                continue

            logger.info(
                "Filtered AgentCore runtime due to tag mismatch: runtimeArn=%s runtimeName=%s required=%s actual=%s",
                runtime_arn,
                runtime_name,
                required_tags,
                runtime_tags,
            )
            filtered_runtimes.append(
                {
                    "runtimeArn": runtime_arn,
                    "runtimeId": runtime_detail.get("agentRuntimeId"),
                    "runtimeName": runtime_name,
                    "serverProtocol": self._extract_runtime_protocol(runtime_detail) or "UNKNOWN",
                    "reason": "tag_filter_mismatch",
                    "requiredTags": required_tags,
                    "actualTags": runtime_tags,
                }
            )

        return matched_details, filtered_runtimes

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
                "runtimeTags": runtime_detail.get("tags", {}),
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
                "runtimeTags": runtime_detail.get("tags", {}),
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
            existing_mcp = await ExtendedMCPServer.find_one({"federationMetadata.runtimeArn": runtime_arn})
            if existing_mcp:
                logger.info(
                    "Runtime type changed to A2A, deleting previous MCP server model for runtimeArn=%s",
                    runtime_arn,
                )
                await existing_mcp.delete()
            return

        if target_type == "mcp":
            existing_a2a = await A2AAgent.find_one({"federationMetadata.runtimeArn": runtime_arn})
            if existing_a2a:
                logger.info(
                    "Runtime type changed to MCP, deleting previous A2A agent model for runtimeArn=%s",
                    runtime_arn,
                )
                await existing_a2a.delete()

    def extract_region_from_arn(self, arn: str, fallback: str = "us-east-1") -> str:
        parts = arn.split(":")
        return parts[3] if len(parts) > 3 and parts[3] else fallback

    def _slug(self, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "-").replace("_", "-")
        return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-/")

    def _build_runtime_invocation_url(self, runtime_arn: str, region: str) -> str:
        escaped_runtime_arn = quote(runtime_arn, safe="")
        return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations"
