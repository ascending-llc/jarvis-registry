from __future__ import annotations

import logging

from ...models import A2AAgent
from ..base_sync_repository import BaseVectorSyncRepository
from ..client import DatabaseClient
from ..sync_result import VectorSyncResult

logger = logging.getLogger(__name__)


class A2AAgentRepository(BaseVectorSyncRepository[A2AAgent]):
    """Vector sync repository for A2A agents."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, A2AAgent)
        logger.info("A2AAgentRepository initialized")

    async def sync_to_vector_db(
        self,
        agent: A2AAgent,
        *,
        is_delete: bool = True,
    ) -> dict:
        """Hash-gated full rebuild for an A2A agent.

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
            new_hash = self._compute_content_hash(agent)
            stored_hash = agent.vectorContentHash

            if stored_hash == new_hash:
                if self._vector_docs_exist("agent_id", agent_id):
                    # Case 4: content unchanged and docs present — skip
                    logger.debug(
                        "Skip vector sync for agent '%s' (agent_id=%s): hash unchanged, docs exist.",
                        agent.card.name,
                        agent_id,
                    )
                    result.skipped = 1
                    return result.to_dict()
                # Case 5: hash matches but Weaviate docs are missing — rebuild silently
                logger.warning(
                    "Hash unchanged but Weaviate docs missing for agent '%s' (agent_id=%s), rebuilding.",
                    agent.card.name,
                    agent_id,
                )

            # Case 1 / 2 / 3 / 5: full rebuild
            if is_delete and self._collection_has_property("agent_id"):
                result.deleted = await self.adelete_by_filter({"agent_id": agent_id})
                if result.deleted:
                    logger.debug("Deleted %d old docs for agent_id=%s", result.deleted, agent_id)

            doc_ids = await self.asave(agent)
            if doc_ids:
                result.indexed = len(doc_ids)
                result.version = self._extract_runtime_version(agent)
                await agent.set({"vectorContentHash": new_hash})
                logger.info(
                    "Indexed %d docs for agent '%s' (agent_id=%s).",
                    result.indexed,
                    agent.card.name,
                    agent_id,
                )
            else:
                result.failed = 1
                logger.error("asave returned no doc_ids for agent '%s' (agent_id=%s).", agent.card.name, agent_id)

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

    async def sync_agent_to_vector_db(
        self,
        agent: A2AAgent,
        is_delete: bool = True,
    ) -> dict:
        return await self.sync_to_vector_db(agent, is_delete=is_delete)

    async def delete_by_agent_id(self, agent_id: str, agent_name: str | None = None) -> int:
        return await self.delete_by_entity_id(agent_id, agent_name)

    @staticmethod
    def _extract_runtime_version(agent: A2AAgent) -> str | None:
        """Extract runtimeVersion / agentVersion from federationMetadata for logging."""
        runtime_version = (agent.federationMetadata or {}).get("runtimeVersion")
        if runtime_version is None:
            runtime_version = (agent.federationMetadata or {}).get("agentVersion")
        return str(runtime_version) if runtime_version is not None else None
