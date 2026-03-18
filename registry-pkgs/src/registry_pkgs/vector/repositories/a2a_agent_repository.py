import logging

from ...models import A2AAgent
from ..client import DatabaseClient
from ..repository import Repository

logger = logging.getLogger(__name__)


class A2AAgentRepository(Repository[A2AAgent]):
    """
    Specialized repository for A2A agent vector sync.
    """

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, A2AAgent)
        logger.info("A2AAgentRepository initialized")

    async def ensure_collection(self) -> bool:
        """
        Ensure collection exists in vector database.
        """
        try:
            if self.adapter.collection_exists(self.collection):
                logger.info("Collection '%s' already exists", self.collection)
                return True

            logger.info("Creating collection '%s'...", self.collection)
            store = self.adapter.get_vector_store(self.collection)
            if store:
                logger.info("Collection '%s' created successfully", self.collection)
                return True
            logger.error("Failed to create collection '%s'", self.collection)
            return False
        except Exception as e:
            logger.error("Error ensuring collection '%s': %s", self.collection, e, exc_info=True)
            raise

    async def sync_agent_to_vector_db(
        self,
        agent: A2AAgent,
        is_delete: bool = True,
    ) -> dict[str, int] | None:
        """
        Full rebuild sync for A2A agent vector docs.
        """
        try:
            collection_existed = self.adapter.collection_exists(self.collection)
            await self.ensure_collection()

            agent_id = str(agent.id) if agent.id else None
            agent_name = agent.card.name

            deleted = 0
            if is_delete and agent_id:
                if not collection_existed:
                    logger.info(
                        "Collection '%s' did not exist before sync. Skip delete for agent_id=%s.",
                        self.collection,
                        agent_id,
                    )
                elif not self.adapter.has_property(self.collection, "agent_id"):
                    logger.info(
                        "Collection '%s' has no 'agent_id' property. Skip delete for agent_id=%s.",
                        self.collection,
                        agent_id,
                    )
                else:
                    deleted = await self.adelete_by_filter({"agent_id": agent_id})
                    if deleted > 0:
                        logger.info("Deleted %s old record(s) by agent_id: %s", deleted, agent_id)

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
            return {"indexed": indexed, "failed": failed, "deleted": deleted}
        except Exception as e:
            logger.error("A2A sync failed for agent %s: %s", agent.card.name, e, exc_info=True)
            return None

    async def delete_by_agent_id(self, agent_id: str, agent_name: str | None = None) -> bool:
        """
        Delete all vector docs for an A2A agent by MongoDB id.
        """
        await self.ensure_collection()
        log_name = f"'{agent_name}' (ID: {agent_id})" if agent_name else f"ID: {agent_id}"

        try:
            if not self.adapter.has_property(self.collection, "agent_id"):
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


def create_a2a_agent_repository(db_client: DatabaseClient) -> A2AAgentRepository:
    return A2AAgentRepository(db_client)


_a2a_agent_repo: A2AAgentRepository | None = None


def get_a2a_agent_repo(db_client: DatabaseClient | None = None) -> A2AAgentRepository:
    global _a2a_agent_repo

    if _a2a_agent_repo is None:
        if db_client is None:
            raise ValueError("db_client is required when initializing A2AAgentRepository")
        _a2a_agent_repo = create_a2a_agent_repository(db_client)

    return _a2a_agent_repo
