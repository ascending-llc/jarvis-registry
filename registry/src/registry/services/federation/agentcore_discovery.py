import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote

from beanie import PydanticObjectId

from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.a2a_agent import AgentConfig

from .agentcore_clients import AgentCoreClientProvider
from .agentcore_runtime_auth import AgentCoreRuntimeAuthService

logger = logging.getLogger(__name__)


class AgentCoreFederationClient:
    """
    Runtime-only AgentCore federation client.

    This client discovers AgentCore runtimes and transforms them into:
    - A2AAgent (for A2A runtimes)
    - ExtendedMCPServer (for MCP runtimes)

    Field convention:
    - AWS SDK payloads use `agentRuntimeArn`
    - Service-layer runtime dictionaries use canonical `runtimeArn`
    """

    def __init__(
        self,
        client_provider: AgentCoreClientProvider | None = None,
    ):
        self.client_provider = client_provider or AgentCoreClientProvider()

    async def discover_runtime_entities(
        self,
        region: str,
        runtime_arns: list[str] | None = None,
        author_id: PydanticObjectId | None = None,
        assume_role_arn: str | None = None,
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

        Important boundary:
        - Discovery is read-only. It must never mutate Mongo state, because the
          same code path is reused by dry-run preview flows.
        - Persisted type reconciliation (for example a runtime switching between
          MCP and A2A) is handled later by the sync apply phase.
        """
        normalized_tag_filter = dict(resource_tags_filter or {})

        try:
            control_client, runtime_summaries = await self.client_provider.execute_with_control_client(
                region,
                lambda control_client: (
                    control_client,
                    self._list_runtime_summaries(control_client),
                ),
                assume_role_arn,
            )
        except Exception as exc:
            logger.error("Failed to list AgentCore runtimes in %s: %s", region, exc, exc_info=True)
            raise RuntimeError(f"Failed to list AgentCore runtimes in {region}: {exc}") from exc

        summary_by_arn = {s["agentRuntimeArn"]: s for s in runtime_summaries if "agentRuntimeArn" in s}
        selected_arns = runtime_arns or list(summary_by_arn.keys())

        selected_summaries: list[dict[str, Any]] = []
        for runtime_arn in selected_arns:
            summary = summary_by_arn.get(runtime_arn)
            if not summary:
                logger.warning("Runtime ARN not found in list_agent_runtimes: %s", runtime_arn)
                continue
            selected_summaries.append(summary)

        control_client, runtime_details = await self.client_provider.execute_with_control_client(
            region,
            lambda control_client: (
                control_client,
                self._get_runtime_details(control_client, selected_summaries),
            ),
            assume_role_arn,
        )
        runtime_details = [self._normalize_runtime_detail(detail) for detail in runtime_details]
        total_candidates = len(runtime_details)
        filtered_out_count = 0

        if normalized_tag_filter:
            logger.info(
                "Applying AgentCore runtime tag filter in region %s: filter=%s total_candidates=%d",
                region,
                normalized_tag_filter,
                total_candidates,
            )
            control_client, filtered = await self.client_provider.execute_with_control_client(
                region,
                lambda control_client: (
                    control_client,
                    self._filter_runtime_details_by_tags(
                        control_client,
                        runtime_details,
                        normalized_tag_filter,
                    ),
                ),
                assume_role_arn,
            )
            runtime_details, filtered_runtimes = filtered
            filtered_out_count = len(filtered_runtimes)
        else:
            filtered_runtimes = []

        logger.info(
            "AgentCore discovery candidates in region %s: total=%d matched_after_tag_filter=%d filtered_out=%d",
            region,
            total_candidates,
            len(runtime_details),
            filtered_out_count,
        )

        a2a_agents: list[A2AAgent] = []
        mcp_servers: list[ExtendedMCPServer] = []
        skipped_runtimes: list[dict[str, Any]] = list(filtered_runtimes)
        logger.debug(f"runtime_details: {runtime_details}")
        for runtime_detail in runtime_details:
            runtime_arn = runtime_detail["runtimeArn"]
            runtime_id = runtime_detail["agentRuntimeId"]
            runtime_name = runtime_detail["agentRuntimeName"]
            protocol = self._extract_runtime_protocol(runtime_detail)

            if protocol == "A2A":
                a2a_agent = self._transform_runtime_to_a2a_agent(runtime_detail, region, author_id)
                a2a_agents.append(a2a_agent)
                continue

            if protocol == "MCP":
                mcp_server = self._transform_runtime_to_mcp_server(runtime_detail, region, author_id)
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
            region,
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
    def _normalize_runtime_detail(runtime_detail: dict[str, Any]) -> dict[str, Any]:
        detail = dict(runtime_detail)
        runtime_arn = detail.get("runtimeArn") or detail.get("agentRuntimeArn")
        if runtime_arn:
            detail["runtimeArn"] = runtime_arn
        return detail

    @staticmethod
    def _matches_resource_tags(runtime_tags: dict[str, str], required_tags: dict[str, str]) -> bool:
        """
        match all tags
        """
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
            runtime_arn = runtime_detail["runtimeArn"]
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

    def _transform_runtime_to_a2a_agent(
        self,
        runtime_detail: dict[str, Any],
        region: str,
        author_id: PydanticObjectId | None = None,
    ) -> A2AAgent:
        runtime_arn = runtime_detail["runtimeArn"]
        runtime_id = runtime_detail["agentRuntimeId"]
        runtime_version = runtime_detail["agentRuntimeVersion"]
        runtime_name = runtime_detail["agentRuntimeName"]
        runtime_base_url = self._build_runtime_invocation_url(
            runtime_arn=runtime_arn,
            region=region,
        )

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
        runtime_access = AgentCoreRuntimeAuthService.infer_runtime_access(
            metadata={"authorizerConfiguration": runtime_detail.get("authorizerConfiguration")},
            runtime_detail=runtime_detail,
        )
        logger.info(
            "AgentCore discovered A2A runtime auth: runtime_arn=%s runtime_name=%s inferred_mode=%s authorizer_configuration=%s protocol_configuration=%s",
            runtime_arn,
            runtime_name,
            runtime_access.mode.value if hasattr(runtime_access.mode, "value") else str(runtime_access.mode),
            json.dumps(runtime_detail.get("authorizerConfiguration"), ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(runtime_detail.get("protocolConfiguration"), ensure_ascii=False, sort_keys=True, default=str),
        )
        return A2AAgent.from_a2a_agent_card(
            card_data=card_data,
            path=f"/agentcore/a2a/{self._slug(runtime_name)}",
            author=author_id or PydanticObjectId(),
            config=AgentConfig(
                title=runtime_name,
                description=runtime_detail.get("description", f"AgentCore runtime {runtime_name}"),
                type="http_json",
                runtimeAccess=runtime_access,
            ),
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
        runtime_arn = runtime_detail["runtimeArn"]
        runtime_id = runtime_detail["agentRuntimeId"]
        runtime_name = runtime_detail["agentRuntimeName"]
        runtime_version = runtime_detail["agentRuntimeVersion"]
        runtime_mcp_url = (
            f"{self._build_runtime_invocation_url(runtime_arn=runtime_arn, region=region)}?qualifier=DEFAULT"
        )
        status = runtime_detail.get("status", "READY")
        runtime_access = AgentCoreRuntimeAuthService.infer_runtime_access(
            metadata={"authorizerConfiguration": runtime_detail.get("authorizerConfiguration")},
            runtime_detail=runtime_detail,
        )
        logger.info(
            "AgentCore discovered MCP runtime auth: runtime_arn=%s runtime_name=%s inferred_mode=%s authorizer_configuration=%s protocol_configuration=%s",
            runtime_arn,
            runtime_name,
            runtime_access.mode.value if hasattr(runtime_access.mode, "value") else str(runtime_access.mode),
            json.dumps(runtime_detail.get("authorizerConfiguration"), ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(runtime_detail.get("protocolConfiguration"), ensure_ascii=False, sort_keys=True, default=str),
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
                "requiresOAuth": False,
                "authProvider": "bedrock-agentcore",
                "runtimeAccess": runtime_access.model_dump(mode="json", exclude_none=True),
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
    def extract_region_from_arn(arn: str) -> str:
        parts = arn.split(":")
        if len(parts) < 6 or parts[0] != "arn" or not parts[2] or not parts[3]:
            raise ValueError(f"Invalid AgentCore runtime ARN: {arn!r}")
        return parts[3]

    def _slug(self, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "-").replace("_", "-")
        return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-/")

    def _build_runtime_invocation_url(
        self,
        runtime_arn: str,
        region: str,
        *,
        decode_for_storage: bool = False,
    ) -> str:
        """Build an AgentCore invocation URL.

        AgentCore data-plane calls must keep the runtime ARN URL-encoded in the
        request path. For persisted config URLs, we intentionally decode it back
        into a human-readable form before saving to Mongo.
        """
        escaped_runtime_arn = quote(runtime_arn, safe="")
        encoded_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations"
        if decode_for_storage:
            return unquote(encoded_url)
        return encoded_url
