import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.models import A2AAgent
from registry_pkgs.models.a2a_agent import AgentSkill

from .azure_ai_foundry_client_provider import AzureAIFoundryClientProvider

logger = logging.getLogger(__name__)


class AzureAIFoundryFederationClient:
    """Discover Azure AI Foundry agents and map them to A2A agents."""

    def __init__(
        self,
        project_endpoint: str,
        metadata_filter: dict[str, str] | None = None,
        client_provider: AzureAIFoundryClientProvider | None = None,
    ):
        self.project_endpoint = project_endpoint.rstrip("/")
        self.metadata_filter = dict(metadata_filter or {})
        self.client_provider = client_provider or AzureAIFoundryClientProvider()

    async def discover_entities(
        self,
        author_id: PydanticObjectId | None = None,
    ) -> dict[str, list[Any]]:
        client = await self.client_provider.get_client(self.project_endpoint)

        try:
            raw_agents = await asyncio.to_thread(self._list_agents, client)
        except Exception as exc:
            logger.error(
                "Failed to list Azure AI Foundry agents from %s: %s",
                self.project_endpoint,
                exc,
                exc_info=True,
            )
            raise RuntimeError(f"Failed to list Azure AI Foundry agents from {self.project_endpoint}: {exc}") from exc

        a2a_agents: list[A2AAgent] = []
        skipped_agents: list[dict[str, Any]] = []

        for raw_agent in raw_agents:
            try:
                if not self._matches_metadata_filter(raw_agent):
                    continue
                mapped = self._transform_agent_to_a2a_agent(raw_agent, author_id)
                if mapped is None:
                    skipped_agents.append(
                        {
                            "agentId": self._read_attr(raw_agent, "id"),
                            "agentName": self._read_attr(raw_agent, "name"),
                            "reason": "missing_required_identity",
                        }
                    )
                    continue
                a2a_agents.append(mapped)
            except Exception as exc:
                skipped_agents.append(
                    {
                        "agentId": self._read_attr(raw_agent, "id"),
                        "agentName": self._read_attr(raw_agent, "name"),
                        "reason": f"transform_error:{exc}",
                    }
                )

        return {"a2a_agents": a2a_agents, "skipped_agents": skipped_agents}

    @staticmethod
    def _list_agents(client: Any) -> list[Any]:
        return list(client.agents.list())

    def _transform_agent_to_a2a_agent(
        self,
        agent: Any,
        author_id: PydanticObjectId | None,
    ) -> A2AAgent | None:
        agent_name = self._read_attr(agent, "name")
        latest_version = self._read_latest_version(agent)
        if not agent_name or latest_version is None:
            return None

        agent_id = self._read_attr(latest_version, "id")
        agent_version = self._read_attr(latest_version, "version")
        if not agent_id or agent_version is None:
            return None

        definition = self._read_attr(latest_version, "definition")
        description = self._read_attr(latest_version, "description") or f"Azure AI Foundry agent {agent_name}"
        created_at = self._read_attr(latest_version, "created_at")
        model = self._read_attr(definition, "model")
        raw_tools = self._normalize_tools(self._read_attr(definition, "tools"))
        skills = self._build_skills(raw_tools)

        card_data = {
            "name": str(agent_name),
            "description": str(description),
            "url": self.project_endpoint,
            "version": str(agent_version or "1"),
            "protocolVersion": "1.0",
            "capabilities": {"streaming": True},
            "skills": skills,
            "securitySchemes": {},
            "preferredTransport": "HTTP+JSON",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["application/json"],
        }

        metadata = {
            "providerType": "azure_ai_foundry",
            "sourceType": "agent",
            "agentName": str(agent_name),
            "agentVersion": str(agent_version) if agent_version is not None else None,
            "agentVersionId": str(agent_id),
            "runtimeArn": str(agent_name),
            "runtimeVersion": str(agent_version) if agent_version is not None else None,
            "model": model,
            "tools": raw_tools,
            "created_at": created_at,
        }

        return A2AAgent.from_a2a_agent_card(
            card_data=card_data,
            path=f"/azure-ai-foundry/a2a/{self._slug(str(agent_name))}",
            author=author_id or PydanticObjectId(),
            isEnabled=True,
            status="active",
            tags=["azure", "ai-foundry", "federated"],
            registeredBy="azure-ai-foundry-federation",
            registeredAt=datetime.now(UTC),
            federationMetadata={k: v for k, v in metadata.items() if v is not None},
        )

    @staticmethod
    def _build_skills(raw_tools: list[dict[str, Any]]) -> list[AgentSkill]:
        skills: list[AgentSkill] = []
        for tool in raw_tools:
            tool_type = str(tool.get("type") or "").lower()
            if tool_type not in {"function", "functiontooldefinition"}:
                continue
            name = tool.get("name") or (tool.get("function") or {}).get("name")
            if not name:
                continue
            description = tool.get("description") or (tool.get("function") or {}).get("description") or ""
            skill_name = str(name)
            skills.append(AgentSkill(id=skill_name, name=skill_name, description=str(description), tags=[]))
        return skills

    @staticmethod
    def _normalize_tools(raw_tools: Any) -> list[dict[str, Any]]:
        if raw_tools is None:
            return []

        normalized: list[dict[str, Any]] = []
        for tool in list(raw_tools):
            if isinstance(tool, dict):
                normalized.append(tool)
                continue
            normalized.append(
                {
                    "type": getattr(tool, "type", None),
                    "name": getattr(tool, "name", None),
                    "description": getattr(tool, "description", None),
                    "function": getattr(tool, "function", None),
                }
            )
        return normalized

    @staticmethod
    def _read_attr(obj: Any, name: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    def _read_latest_version(self, agent: Any) -> Any | None:
        versions = self._read_attr(agent, "versions")
        if versions is None:
            return None
        return self._read_attr(versions, "latest")

    def _matches_metadata_filter(self, agent: Any) -> bool:
        if not self.metadata_filter:
            return True

        latest_version = self._read_latest_version(agent)
        if latest_version is None:
            return False

        metadata = self._read_attr(latest_version, "metadata") or {}
        if not isinstance(metadata, dict):
            return False

        for key, expected_value in self.metadata_filter.items():
            if str(metadata.get(key)) != str(expected_value):
                return False
        return True

    @staticmethod
    def _slug(value: str) -> str:
        cleaned = re.sub(r"[\s_]+", "-", value.strip().lower())
        return re.sub(r"[^a-z0-9-]", "", cleaned)
