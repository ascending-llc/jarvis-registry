from __future__ import annotations

import asyncio
import logging

from ...models import ExtendedMCPServer
from ..base_sync_repository import BaseVectorSyncRepository
from ..client import DatabaseClient
from ..sync_result import VectorSyncResult

logger = logging.getLogger(__name__)


class MCPServerRepository(BaseVectorSyncRepository[ExtendedMCPServer]):
    """Vector sync repository for MCP servers."""

    FILTERABLE_PROPERTIES = {
        "server_id": "text",
        "federation_id": "text",
        "runtimeArn": "text",
        "path": "text",
        "enabled": "bool",
    }

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
                try:
                    result.deleted = await asyncio.wait_for(
                        self.adelete_by_filter({"server_id": server_id}),
                        timeout=10.0,
                    )
                    if result.deleted:
                        logger.debug("Deleted %d old docs for server_id=%s", result.deleted, server_id)
                except TimeoutError:
                    logger.warning(
                        "adelete_by_filter timed out for server_id=%s — skipping delete, proceeding with insert",
                        server_id,
                    )

            docs = server.to_documents()
            if not docs:
                logger.info(
                    "Server '%s' (server_id=%s) has no tools/resources/prompts — nothing to index.",
                    server.serverName,
                    server_id,
                )
                return result.to_dict_mcp()

            expected = len(docs)
            doc_ids = await self.asave(server)
            if not doc_ids:
                result.failed = expected
                result.error = "asave returned no doc_ids"
                logger.error("asave returned no doc_ids for server '%s' (server_id=%s).", server.serverName, server_id)
                return result.to_dict_mcp()
            try:
                landed_docs = await self.afilter(filters={"server_id": server_id}, limit=expected + 1)
                actual = len(landed_docs)
            except Exception as e:
                logger.warning(
                    "Post-insert verification failed for server '%s' (server_id=%s): %s — falling back to doc_ids count",
                    server.serverName,
                    server_id,
                    e,
                )
                actual = len(doc_ids)

            if actual >= expected:
                result.indexed = actual
                result.version = self._extract_runtime_version(server)
                logger.info(
                    "Indexed %d docs for server '%s' (server_id=%s).",
                    result.indexed,
                    server.serverName,
                    server_id,
                )
            else:
                result.indexed = actual
                result.failed = expected - actual
                result.error = (
                    f"only {actual}/{expected} docs landed in Weaviate "
                    "(check langchain-weaviate ERROR logs for batch insert failures)"
                )
                logger.error(
                    "Vector sync partial failure for server '%s' (server_id=%s): %d/%d docs inserted",
                    server.serverName,
                    server_id,
                    actual,
                    expected,
                )

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
        deleted = await self.adelete_by_filter({"server_id": entity_id})
        logger.info(
            "Deleted %d Weaviate docs for server '%s' (server_id=%s).",
            deleted,
            entity_name or "?",
            entity_id,
        )
        return deleted

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
