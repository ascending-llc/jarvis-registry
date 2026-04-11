import logging

from langchain_core.documents import Document as LangChainDocument

from ...models import A2AAgent
from ..client import DatabaseClient
from ..repository import Repository

logger = logging.getLogger(__name__)


class A2AAgentRepository(Repository[A2AAgent]):
    """
    Specialized repository for A2A agent vector sync.
    """

    VECTOR_SCAN_BATCH_SIZE = 500

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, A2AAgent)
        logger.info("A2AAgentRepository initialized")

    @staticmethod
    def _extract_runtime_version(agent: A2AAgent) -> str | None:
        runtime_version = (agent.federationMetadata or {}).get("runtimeVersion")
        if runtime_version is None:
            runtime_version = (agent.federationMetadata or {}).get("agentVersion")
        if runtime_version is None:
            return None
        return str(runtime_version)

    def _load_existing_docs(self, agent_id: str) -> list[LangChainDocument]:
        if not self._collection_has_property("agent_id"):
            return []
        docs = self._load_docs_by_metadata_paginated(
            filters={"agent_id": agent_id},
            batch_size=self.VECTOR_SCAN_BATCH_SIZE,
        )

        if docs:
            logger.debug(
                "Loaded %d existing A2A vector docs for agent_id=%s using paginated metadata scan.",
                len(docs),
                agent_id,
            )
        return docs

    def _runtime_version_property_names(self) -> list[str]:
        property_names: list[str] = []
        if self._collection_has_property("agentVersion"):
            property_names.append("agentVersion")
        if self._collection_has_property("runtimeVersion"):
            property_names.append("runtimeVersion")
        return property_names

    def _should_skip_reindex(self, agent: A2AAgent, agent_id: str) -> tuple[bool, str | None]:
        current_version = self._extract_runtime_version(agent)
        if not current_version:
            return False, None
        version_properties = self._runtime_version_property_names()
        if not version_properties:
            return False, current_version

        existing_docs = self._load_existing_docs(agent_id)
        if not existing_docs:
            return False, current_version

        expected_docs = agent.to_documents()
        existing_versions = {
            str(doc.metadata.get(version_property))
            for doc in existing_docs
            for version_property in version_properties
            if doc.metadata.get(version_property) is not None
        }

        if len(existing_docs) != len(expected_docs):
            logger.info(
                "Rebuild A2A vector docs for agent '%s' (agent_id=%s) because document count changed: mongo=%d weaviate=%d",
                agent.card.name,
                agent_id,
                len(expected_docs),
                len(existing_docs),
            )
            return False, current_version

        if existing_versions == {current_version}:
            logger.info(
                "Skip A2A vector rebuild for agent '%s' (agent_id=%s) because runtime_version=%s already matches Weaviate.",
                agent.card.name,
                agent_id,
                current_version,
            )
            return True, current_version

        logger.info(
            "Rebuild A2A vector docs for agent '%s' (agent_id=%s) because runtime_version differs: mongo=%s weaviate=%s",
            agent.card.name,
            agent_id,
            current_version,
            sorted(existing_versions) if existing_versions else [],
        )
        return False, current_version

    async def ensure_collection(self) -> bool:
        """
        Ensure collection exists in vector database.
        """
        return await self._ensure_collection()

    async def sync_agent_to_vector_db(
        self,
        agent: A2AAgent,
        is_delete: bool = True,
    ) -> dict[str, int | str | None]:
        """
        Full rebuild sync for A2A agent vector docs.
        """
        try:
            collection_existed = self.adapter.collection_exists(self.collection)
            await self.ensure_collection()

            agent_id = str(agent.id) if agent.id else None
            agent_name = agent.card.name

            deleted = 0
            skipped = 0
            if is_delete and agent_id:
                if not collection_existed:
                    logger.info(
                        "Collection '%s' did not exist before sync. Skip delete for agent_id=%s.",
                        self.collection,
                        agent_id,
                    )
                elif not self._collection_has_property("agent_id"):
                    logger.info(
                        "Collection '%s' has no 'agent_id' property. Skip delete for agent_id=%s.",
                        self.collection,
                        agent_id,
                    )
                else:
                    deleted = await self.adelete_by_filter({"agent_id": agent_id})
                    if deleted > 0:
                        logger.info("Deleted %s old record(s) by agent_id: %s", deleted, agent_id)
            elif not is_delete and agent_id:
                should_skip, current_version = self._should_skip_reindex(agent, agent_id)
                if should_skip:
                    skipped = 1
                    return {
                        "indexed": 0,
                        "failed": 0,
                        "deleted": 0,
                        "skipped": skipped,
                        "version": current_version,
                        "error": None,
                    }

            doc_ids = await self.asave(agent)
            success = bool(doc_ids)
            indexed = len(doc_ids) if doc_ids else 0
            failed = 0 if success else 1

            logger.info(
                "Indexed A2A agent '%s' (agent_id: %s): %s",
                agent_name,
                agent_id,
                "success" if success else "failed",
            )
            return {
                "indexed": indexed,
                "failed": failed,
                "deleted": deleted,
                "skipped": skipped,
                "version": self._extract_runtime_version(agent),
                "error": None,
            }
        except Exception as e:
            logger.error("A2A sync failed for agent %s: %s", agent.card.name, e, exc_info=True)
            return {"indexed": 0, "failed": 1, "deleted": 0, "skipped": 0, "version": None, "error": str(e)}

    async def delete_by_agent_id(self, agent_id: str, agent_name: str | None = None) -> bool:
        """
        Delete all vector docs for an A2A agent by MongoDB id.
        """
        await self.ensure_collection()
        log_name = f"'{agent_name}' (ID: {agent_id})" if agent_name else f"ID: {agent_id}"

        try:
            if not self._collection_has_property("agent_id"):
                logger.info(
                    "Collection '%s' has no 'agent_id' property. Skip delete for %s.", self.collection, log_name
                )
                return True

            deleted = await self.adelete_by_filter({"agent_id": agent_id})
            logger.info("Deleted %s A2A vector document(s) for %s", deleted, log_name)
            return True
        except Exception as e:
            logger.error("Failed to delete A2A vector docs for %s: %s", log_name, e, exc_info=True)
            return False

    async def delete_by_runtime_identity(self, federation_id: str, runtime_arn: str) -> int:
        """Delete one federated A2A runtime slice from Weaviate by federation + runtime."""
        if not self._collection_has_property("federation_id") or not self._collection_has_property("runtimeArn"):
            logger.info(
                "Collection '%s' missing federation_id/runtimeArn. Skip runtime delete for federation_id=%s runtimeArn=%s.",
                self.collection,
                federation_id,
                runtime_arn,
            )
            return 0
        return await self.adelete_by_filter({"federation_id": federation_id, "runtimeArn": runtime_arn})

    def has_runtime_identity(self, federation_id: str, runtime_arn: str) -> bool:
        """Return whether Weaviate already contains docs for one federated A2A runtime."""
        if not self._collection_has_property("federation_id") or not self._collection_has_property("runtimeArn"):
            return False
        docs = self.adapter.filter_by_metadata(
            filters={"federation_id": federation_id, "runtimeArn": runtime_arn},
            limit=1,
            collection_name=self.collection,
        )
        return bool(docs)
