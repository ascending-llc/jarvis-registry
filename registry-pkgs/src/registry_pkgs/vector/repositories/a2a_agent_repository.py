from __future__ import annotations

import asyncio
import logging

from ...models import A2AAgent
from ..base_sync_repository import BaseVectorSyncRepository
from ..client import DatabaseClient
from ..sync_result import VectorSyncResult

logger = logging.getLogger(__name__)


class A2AAgentRepository(BaseVectorSyncRepository[A2AAgent]):
    """Vector sync repository for A2A agents."""

    FILTERABLE_PROPERTIES = {
        "agent_id": "text",
        "federation_id": "text",
        "runtimeArn": "text",
        "enabled": "bool",
    }

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, A2AAgent)
        logger.info("A2AAgentRepository initialized")

    async def sync_to_vector_db(
        self,
        agent: A2AAgent,
        *,
        is_delete: bool = True,
    ) -> dict:
        """Full rebuild for an A2A agent — caller must ensure content actually changed.

        Args:
            agent: Agent instance with current MongoDB state.
            is_delete: True (CRUD) — delete old docs before reinserting.
                       False (federation) — external caller already deleted; insert only.
        """
        result = VectorSyncResult()
        try:
            agent_id = str(agent.id) if agent.id else None
            if not agent_id:
                result.failed = 1
                result.error = "agent has no id"
                return result.to_dict()

            await self.ensure_collection()

            if is_delete and self._collection_has_property("agent_id"):
                try:
                    result.deleted = await asyncio.wait_for(
                        self.adelete_by_filter({"agent_id": agent_id}),
                        timeout=10.0,
                    )
                    if result.deleted:
                        logger.debug("Deleted %d old docs for agent_id=%s", result.deleted, agent_id)
                except TimeoutError:
                    logger.warning(
                        "adelete_by_filter timed out for agent_id=%s — skipping delete, proceeding with insert",
                        agent_id,
                    )

            docs = agent.to_documents()
            expected = len(docs) if docs else 0
            doc_ids = await self.asave(agent)
            if not doc_ids:
                result.failed = max(expected, 1)
                result.error = "asave returned no doc_ids"
                logger.error("asave returned no doc_ids for agent '%s' (agent_id=%s).", agent.card.name, agent_id)
                return result.to_dict()
            verified = False
            try:
                landed_docs = await self.afilter(filters={"agent_id": agent_id}, limit=expected)
                verified = len(landed_docs) >= expected
            except Exception as e:
                logger.warning(
                    "Post-insert verification failed for agent '%s' (agent_id=%s): %s — trusting asave return value",
                    agent.card.name,
                    agent_id,
                    e,
                )
                verified = True

            if verified:
                result.indexed = len(doc_ids)
                result.version = self._extract_runtime_version(agent)
                logger.info(
                    "Indexed %d docs for agent '%s' (agent_id=%s).",
                    result.indexed,
                    agent.card.name,
                    agent_id,
                )
            else:
                result.indexed = 0
                result.failed = expected
                result.error = (
                    f"asave returned {len(doc_ids)} UUIDs but fewer than {expected} docs are queryable "
                    "in Weaviate (check langchain-weaviate ERROR logs for batch insert failures)"
                )
                logger.error(
                    "Vector sync verification failed for agent '%s' (agent_id=%s): expected %d docs",
                    agent.card.name,
                    agent_id,
                    expected,
                )
        except Exception as e:
            logger.error("A2A vector sync failed for agent %s: %s", getattr(agent, "id", "?"), e, exc_info=True)
            result.failed = 1
            result.error = str(e)
        return result.to_dict()

    async def delete_by_entity_id(self, entity_id: str, entity_name: str | None = None) -> int:
        """Remove all Weaviate docs for an A2A agent."""
        await self.ensure_collection()
        if not self._collection_has_property("agent_id"):
            logger.info(
                "Collection '%s' has no 'agent_id' property, skipping delete for agent %s.",
                self.collection,
                entity_name or entity_id,
            )
            return 0
        deleted = await self.adelete_by_filter({"agent_id": entity_id})
        logger.info(
            "Deleted %d Weaviate docs for agent '%s' (agent_id=%s).",
            deleted,
            entity_name or "?",
            entity_id,
        )
        return deleted

    async def delete_by_agent_id(self, agent_id: str, agent_name: str | None = None) -> int:
        return await self.delete_by_entity_id(agent_id, agent_name)

    @staticmethod
    def _extract_runtime_version(agent: A2AAgent) -> str | None:
        """Extract runtimeVersion / agentVersion from federationMetadata for logging."""
        runtime_version = (agent.federationMetadata or {}).get("runtimeVersion")
        if runtime_version is None:
            runtime_version = (agent.federationMetadata or {}).get("agentVersion")
        return str(runtime_version) if runtime_version is not None else None
