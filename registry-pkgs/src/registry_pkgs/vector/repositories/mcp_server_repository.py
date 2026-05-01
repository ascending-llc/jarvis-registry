from __future__ import annotations

import logging

from ...models import ExtendedMCPServer
from ...models.enums import MCPEntityType
from ..base_sync_repository import BaseVectorSyncRepository
from ..client import DatabaseClient
from ..sync_result import VectorSyncResult

logger = logging.getLogger(__name__)


class MCPServerRepository(BaseVectorSyncRepository[ExtendedMCPServer]):
    """Vector sync repository for MCP servers."""

    def __init__(self, db_client: DatabaseClient):
        super().__init__(db_client, ExtendedMCPServer)
        logger.info("MCPServerRepository initialized")

    async def sync_to_vector_db(
        self,
        server: ExtendedMCPServer,
        *,
        is_delete: bool = True,
    ) -> dict:
        """Full rebuild for an MCP server — caller must ensure content actually changed.

        Args:
            server: Server instance with current MongoDB state.
            is_delete: True (CRUD) — delete old docs before reinserting.
                       False (federation) — external caller already deleted; insert only.
        """
        result = VectorSyncResult()
        try:
            server_id = str(server.id) if server.id else None
            if not server_id:
                result.failed = 1
                result.error = "server has no id"
                return result.to_dict_mcp()

            await self.ensure_collection()

            if is_delete and self._collection_has_property("server_id"):
                result.deleted = await self.adelete_by_filter({"server_id": server_id})
                if result.deleted:
                    logger.debug("Deleted %d old docs for server_id=%s", result.deleted, server_id)

            docs = server.to_documents()
            if not docs:
                logger.info(
                    "Server '%s' (server_id=%s) has no tools/resources/prompts — nothing to index.",
                    server.serverName,
                    server_id,
                )
                return result.to_dict_mcp()

            doc_ids = await self.asave(server)
            if doc_ids:
                result.indexed = len(doc_ids)
                result.version = self._extract_runtime_version(server)
                logger.info(
                    "Indexed %d docs for server '%s' (server_id=%s).",
                    result.indexed,
                    server.serverName,
                    server_id,
                )
            else:
                result.failed = 1
                logger.error("asave returned no doc_ids for server '%s' (server_id=%s).", server.serverName, server_id)

        except Exception as e:
            logger.error("MCP vector sync failed for server %s: %s", getattr(server, "id", "?"), e, exc_info=True)
            result.failed = 1
            result.error = str(e)

        return result.to_dict_mcp()

    async def delete_by_entity_id(self, entity_id: str, entity_name: str | None = None) -> int:
        """Remove all Weaviate docs for an MCP server."""
        await self.ensure_collection()
        if not self._collection_has_property("server_id"):
            logger.info(
                "Collection '%s' has no 'server_id' property, skipping delete for server %s.",
                self.collection,
                entity_name or entity_id,
            )
            return 0
        total_deleted = 0
        for entity_type in MCPEntityType:
            docs = self.adapter.filter_by_metadata(
                filters={"server_id": entity_id, "entity_type": entity_type.value},
                limit=1000,
                collection_name=self.collection,
            )
            if docs:
                self.adapter.delete(ids=[doc.id for doc in docs], collection_name=self.collection)
                total_deleted += len(docs)
                logger.debug("[%s] Deleted %d docs for server_id=%s", entity_type.value, len(docs), entity_id)
        logger.info(
            "Deleted %d Weaviate docs for server '%s' (server_id=%s).",
            total_deleted,
            entity_name or "?",
            entity_id,
        )
        return total_deleted

    async def delete_by_server_id(self, server_id: str, server_name: str | None = None) -> int:
        return await self.delete_by_entity_id(server_id, server_name)

    async def get_by_server_id(self, server_id: str) -> ExtendedMCPServer | None:
        try:
            results = await self.afilter(filters={"server_id": server_id}, limit=1)
            return results[0] if results else None
        except Exception as e:
            logger.error("Get by server_id failed: %s", e)
            return None

    @staticmethod
    def _extract_runtime_version(server: ExtendedMCPServer) -> str | None:
        runtime_version = (server.federationMetadata or {}).get("runtimeVersion")
        return str(runtime_version) if runtime_version is not None else None
