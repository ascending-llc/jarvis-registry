from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from a2a.client import A2ACardResolver
from azure.ai.projects.aio import AIProjectClient
from beanie import PydanticObjectId

from registry_pkgs.models import A2AAgent
from registry_pkgs.models.a2a_agent import (
    AgentConfig,
    preferred_transport_to_config_type,
)
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig

from .azure_foundry_auth import AzureFoundryAuthService

logger = logging.getLogger(__name__)


A2A_PROTOCOL_VALUE = "a2a"
AGENT_CARD_PATH = "agentCard/v0.3"
PROVIDER_TAGS = ["azure", "foundry", "a2a", "federated"]


class AzureFoundryDiscoveryClient:
    """Discover A2A-enabled Foundry agents and project them onto A2AAgent.

    Discovery is read-only — never mutates Mongo state (dry-run reuses this path).
    """

    async def discover_a2a_agents(
        self,
        *,
        provider_config: AzureAiFoundryProviderConfig,
        auth: AzureFoundryAuthService,
        author_id: PydanticObjectId | None = None,
    ) -> list[A2AAgent]:
        project_endpoint = provider_config.projectEndpoint
        if not project_endpoint:
            raise ValueError("Azure AI Foundry providerConfig.projectEndpoint is required")

        credential = auth.credential()

        async with AIProjectClient(endpoint=project_endpoint, credential=credential) as project:
            names = await self._collect_agent_names(project, provider_config)
            details = await self._fetch_agent_details(project, names)

        a2a_details = [detail for detail in details if self._is_a2a_enabled(detail)]
        if not a2a_details:
            logger.info(
                "Azure AI Foundry discovery: project=%s listed=%d a2a_enabled=0",
                project_endpoint,
                len(details),
            )
            return []

        filter_kv = dict(provider_config.metadataFilter or {})
        if filter_kv:
            a2a_details = [d for d in a2a_details if self._matches_metadata_filter(d, filter_kv)]

        logger.info(
            "Azure AI Foundry discovery: project=%s listed=%d a2a_enabled=%d after_metadata_filter=%d",
            project_endpoint,
            len(details),
            sum(1 for d in details if self._is_a2a_enabled(d)),
            len(a2a_details),
        )

        agents = [
            self._transform_to_a2a_agent(
                detail=detail,
                project_endpoint=project_endpoint,
                author_id=author_id,
            )
            for detail in a2a_details
        ]

        # Enrich with the full agentCard/v0.3 (parallel; failures kept per-agent).
        headers = await auth.build_headers()
        async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60.0)) as http_client:
            await asyncio.gather(
                *(self._enrich_agent_card(agent, http_client) for agent in agents),
                return_exceptions=False,
            )

        return agents

    async def _collect_agent_names(
        self,
        project: AIProjectClient,
        provider_config: AzureAiFoundryProviderConfig,
    ) -> list[str]:
        configured_names = [name for name in (provider_config.agentNames or []) if name]
        if configured_names:
            return list(dict.fromkeys(configured_names))

        try:
            names: list[str] = []
            async for agent in project.agents.list():
                name = getattr(agent, "name", None)
                if name:
                    names.append(name)
            return names
        except Exception as exc:
            logger.error("Failed to list Azure AI Foundry agents: %s", exc, exc_info=True)
            raise RuntimeError(f"Failed to list Azure AI Foundry agents: {exc}") from exc

    async def _fetch_agent_details(
        self,
        project: AIProjectClient,
        names: list[str],
    ) -> list[Any]:
        if not names:
            return []

        async def _safe_get(name: str) -> Any | None:
            try:
                return await project.agents.get(name)
            except Exception as exc:
                # A single broken agent must not abort the whole sync. Log and skip.
                logger.warning("agents.get(%s) failed: %s", name, exc)
                return None

        results = await asyncio.gather(*(_safe_get(name) for name in names))
        return [detail for detail in results if detail is not None]

    @staticmethod
    def _is_a2a_enabled(detail: Any) -> bool:
        endpoint = getattr(detail, "agent_endpoint", None)
        protocols = getattr(endpoint, "protocols", None) or []
        return any(str(p).lower() == A2A_PROTOCOL_VALUE for p in protocols)

    @staticmethod
    def _matches_metadata_filter(detail: Any, required: dict[str, str]) -> bool:
        latest = AzureFoundryDiscoveryClient._latest_version(detail) or {}
        metadata = latest.get("metadata") if isinstance(latest, dict) else None
        metadata = metadata or {}
        return all(str(metadata.get(key)) == str(expected) for key, expected in required.items())

    @staticmethod
    def _latest_version(detail: Any) -> dict[str, Any] | None:
        versions = getattr(detail, "versions", None)
        latest = getattr(versions, "latest", None)
        if latest is None:
            return None
        if hasattr(latest, "as_dict"):
            try:
                return latest.as_dict()
            except Exception as e:
                logger.error("Failed to get latest version: %s", e)
        if hasattr(latest, "model_dump"):
            return latest.model_dump(mode="json")
        if isinstance(latest, dict):
            return latest
        return None

    def _transform_to_a2a_agent(
        self,
        *,
        detail: Any,
        project_endpoint: str,
        author_id: PydanticObjectId | None,
    ) -> A2AAgent:
        name = getattr(detail, "name", None)
        if not name:
            raise ValueError("Azure AI Foundry agent detail missing 'name'")

        latest = self._latest_version(detail) or {}
        version = str(latest.get("version") or "")
        status = str(latest.get("status") or "active").lower()
        is_ready = status == "active"
        agent_guid = latest.get("agent_guid")
        version_id = latest.get("id")
        created_at = latest.get("created_at")
        modified_at = (latest.get("metadata") or {}).get("modified_at") if isinstance(latest, dict) else None

        a2a_base_url = self._build_a2a_base_url(project_endpoint, name)

        endpoint = getattr(detail, "agent_endpoint", None)
        authorization_schemes = self._serialize_authorization_schemes(endpoint)
        embedded_card = self._embedded_card_payload(detail)
        skills = embedded_card.get("skills") or []
        description = embedded_card.get("description") or latest.get("description") or ""

        card_data: dict[str, Any] = {
            "name": name,
            "description": description or f"Azure Foundry agent {name}",
            "url": a2a_base_url,
            "version": embedded_card.get("version") or version or "0",
            "protocolVersion": "0.3",
            "capabilities": {"streaming": False},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": skills,
            "preferredTransport": "JSONRPC",
        }

        return A2AAgent.from_a2a_agent_card(
            card_data=card_data,
            path=f"/{self._slug(name)}",
            author=author_id or PydanticObjectId(),
            config=AgentConfig(
                title=name,
                description=description or f"Azure Foundry agent {name}",
                url=a2a_base_url,
                type=preferred_transport_to_config_type("JSONRPC"),
            ),
            isEnabled=is_ready,
            status="active" if is_ready else "inactive",
            tags=list(PROVIDER_TAGS),
            registeredBy="azure-foundry-federation",
            registeredAt=datetime.now(UTC),
            federationMetadata={
                "providerType": FederationProviderType.AZURE_AI_FOUNDRY.value,
                # D2: write the agent name to runtimeArn so the existing
                # vector-rebuild query keyed by federationMetadata.runtimeArn
                # also matches Azure resources.
                "runtimeArn": name,
                "agentName": name,
                "agentVersion": version,
                "agentGuid": agent_guid,
                "versionId": version_id,
                "status": status,
                "createdAt": created_at,
                "modifiedAt": modified_at,
                "projectEndpoint": project_endpoint,
                "a2aBaseUrl": a2a_base_url,
                "agentCardPath": AGENT_CARD_PATH,
                "authorizationSchemes": authorization_schemes,
            },
            wellKnown={
                "enabled": True,
                "url": f"{a2a_base_url}/{AGENT_CARD_PATH}",
                "lastSyncStatus": "pending",
                "lastSyncVersion": version,
                "syncError": None,
                "lastSyncAt": None,
            },
        )

    async def _enrich_agent_card(
        self,
        agent: A2AAgent,
        http_client: httpx.AsyncClient,
    ) -> None:
        metadata = dict(agent.federationMetadata or {})
        a2a_base_url = metadata.get("a2aBaseUrl")
        if not a2a_base_url:
            self._mark_enrichment_failure(agent, "missing a2aBaseUrl")
            return

        try:
            resolver = A2ACardResolver(
                httpx_client=http_client,
                base_url=a2a_base_url,
                agent_card_path=AGENT_CARD_PATH,
            )
            card = await resolver.get_agent_card()
        except Exception as exc:
            logger.warning("Foundry A2A enrichment failed for %s: %s", agent.card.name, exc)
            self._mark_enrichment_failure(agent, str(exc))
            return

        card_payload = card.model_dump(mode="json", by_alias=True, exclude_none=True)
        fallback = agent.card.model_dump(mode="json")
        # Preserve our authoritative URL (Foundry returns the same value but we
        # don't want a remote rewrite to drift it on enrichment failure paths).
        merged = {**fallback, **card_payload, "url": a2a_base_url}

        refreshed = A2AAgent.from_a2a_agent_card(
            card_data=merged,
            path=agent.path,
            author=agent.author,
            config=agent.config
            or AgentConfig(
                title=fallback.get("name", agent.card.name),
                description=fallback.get("description", "") or "",
                type=preferred_transport_to_config_type("JSONRPC"),
            ),
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
        if agent.config is not None:
            preferred = str(getattr(agent.card, "preferred_transport", None) or "JSONRPC").upper()
            agent.config.type = preferred_transport_to_config_type(preferred)
            agent.config.url = a2a_base_url

        if agent.wellKnown is not None:
            agent.wellKnown.lastSyncStatus = "success"
            agent.wellKnown.syncError = None
            agent.wellKnown.lastSyncAt = datetime.now(UTC)
            agent.wellKnown.lastSyncVersion = str(agent.card.version)

        cleared = dict(agent.federationMetadata or {})
        cleared.pop("enrichmentError", None)
        agent.federationMetadata = cleared

    @staticmethod
    def _mark_enrichment_failure(agent: A2AAgent, message: str) -> None:
        if agent.wellKnown is not None:
            agent.wellKnown.lastSyncStatus = "failed"
            agent.wellKnown.syncError = message
            agent.wellKnown.lastSyncAt = datetime.now(UTC)
        metadata = dict(agent.federationMetadata or {})
        metadata["enrichmentError"] = f"a2a enrichment failed: {message}"
        agent.federationMetadata = metadata

    @staticmethod
    def _embedded_card_payload(detail: Any) -> dict[str, Any]:
        card = getattr(detail, "agent_card", None)
        if card is None:
            return {}
        if hasattr(card, "as_dict"):
            try:
                return card.as_dict() or {}
            except Exception as e:
                logger.error(f"Failed to embed card payload: {e}")
        if hasattr(card, "model_dump"):
            return card.model_dump(mode="json")
        if isinstance(card, dict):
            return card
        return {}

    @staticmethod
    def _serialize_authorization_schemes(endpoint: Any) -> list[dict[str, Any]]:
        schemes = getattr(endpoint, "authorization_schemes", None) or []
        result: list[dict[str, Any]] = []
        for scheme in schemes:
            if hasattr(scheme, "as_dict"):
                try:
                    result.append(scheme.as_dict())
                    continue
                except Exception as e:
                    logger.warning(f"{type(e)}: {e}")
            if hasattr(scheme, "model_dump"):
                result.append(scheme.model_dump(mode="json"))
                continue
            if isinstance(scheme, dict):
                result.append(scheme)
        return result

    @staticmethod
    def _build_a2a_base_url(project_endpoint: str, agent_name: str) -> str:
        return f"{project_endpoint.rstrip('/')}/agents/{agent_name}/endpoint/protocols/a2a"

    @staticmethod
    def _slug(value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "-").replace("_", "-")
        return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-/")
