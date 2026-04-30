from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document

from ...models import ExtendedMCPServer
from ...models.enums import ServerEntityType
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
        """Hash-gated full rebuild for an MCP server.

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
            new_hash = self._compute_content_hash(server)
            stored_hash = server.vectorContentHash

            if stored_hash == new_hash:
                if self._vector_docs_exist("server_id", server_id):
                    # Case 4: content unchanged and docs present — skip
                    logger.debug(
                        "Skip vector sync for server '%s' (server_id=%s): hash unchanged, docs exist.",
                        server.serverName,
                        server_id,
                    )
                    result.skipped = 1
                    return result.to_dict_mcp()
                # Case 5: hash matches but Weaviate docs are missing — rebuild silently
                logger.warning(
                    "Hash unchanged but Weaviate docs missing for server '%s' (server_id=%s), rebuilding.",
                    server.serverName,
                    server_id,
                )

            # Case 1 / 2 / 3 / 5: full rebuild
            if is_delete and self._collection_has_property("server_id"):
                result.deleted = await self.adelete_by_filter({"server_id": server_id})
                if result.deleted:
                    logger.debug("Deleted %d old docs for server_id=%s", result.deleted, server_id)

            doc_ids = await self.asave(server)
            if doc_ids:
                result.indexed = len(doc_ids)
                result.version = self._extract_runtime_version(server)
                await server.set({"vectorContentHash": new_hash})
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
        for entity_type in ServerEntityType:
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

    async def sync_server_to_vector_db(
        self,
        server: ExtendedMCPServer,
        is_delete: bool = True,
    ) -> dict:
        return await self.sync_to_vector_db(server, is_delete=is_delete)

    async def delete_by_server_id(self, server_id: str, server_name: str | None = None) -> int:
        return await self.delete_by_entity_id(server_id, server_name)

    async def get_by_server_id(self, server_id: str) -> ExtendedMCPServer | None:
        try:
            results = await self.afilter(filters={"server_id": server_id}, limit=1)
            return results[0] if results else None
        except Exception as e:
            logger.error("Get by server_id failed: %s", e)
            return None

    async def get_all_docs_by_server_id(self, server_id: str) -> dict[str, list[Any]]:
        """Get all vector documents grouped by entity type."""
        try:
            result: dict[str, list[Any]] = {"tools": [], "resources": [], "prompts": []}
            for entity_type in ServerEntityType:
                docs = self.adapter.filter_by_metadata(
                    filters={"server_id": server_id, "entity_type": entity_type.value},
                    limit=1000,
                    collection_name=self.collection,
                )
                result[f"{entity_type.value}s"] = docs
            logger.info(
                "Retrieved docs for server_id %s: tools=%d, resources=%d, prompts=%d",
                server_id,
                len(result["tools"]),
                len(result["resources"]),
                len(result["prompts"]),
            )
            return result
        except Exception as e:
            logger.error("get_all_docs_by_server_id failed: %s", e, exc_info=True)
            return {"tools": [], "resources": [], "prompts": []}

    async def smart_sync(self, server: ExtendedMCPServer) -> bool:
        """Incremental sync comparing docs per entity type — avoids re-embedding unchanged tools."""
        await self.ensure_collection()
        server_id = str(server.id)
        try:
            new_docs = server.to_documents()
            new_docs_by_type = self._group_docs_by_entity_type(new_docs)
            total_added = total_deleted = total_updated = 0

            for entity_type in ServerEntityType:
                et = entity_type.value
                existing_docs = self.adapter.filter_by_metadata(
                    filters={"server_id": server_id, "entity_type": et},
                    limit=10000,
                    collection_name=self.collection,
                )
                new_docs_for_type = new_docs_by_type.get(et, [])

                if not existing_docs and not new_docs_for_type:
                    continue
                elif not existing_docs and new_docs_for_type:
                    ids = self.adapter.add_documents(documents=new_docs_for_type, collection_name=self.collection)
                    total_added += len(ids) if ids else 0
                elif existing_docs and not new_docs_for_type:
                    self.adapter.delete(ids=[d.id for d in existing_docs], collection_name=self.collection)
                    total_deleted += len(existing_docs)
                else:
                    added, deleted, updated = await self._sync_entity_type(et, existing_docs, new_docs_for_type)
                    total_added += added
                    total_deleted += deleted
                    total_updated += updated

            logger.info(
                "Smart sync completed for '%s': added=%d, deleted=%d, updated=%d",
                server.serverName,
                total_added,
                total_deleted,
                total_updated,
            )
            return True
        except Exception as e:
            logger.error("Smart sync failed for '%s' (id=%s): %s", server.serverName, server_id, e, exc_info=True)
            return False

    async def sync_by_enabled_status(self, server: ExtendedMCPServer, enabled: bool) -> bool:
        """Metadata-only update when disabled; smart sync when re-enabled (federation path)."""
        await self.ensure_collection()
        server_id = str(server.id)
        try:
            if not enabled:
                logger.info("Server disabled, updating metadata only for '%s' (id=%s)", server.serverName, server_id)
                return await self._update_metadata_by_entity_type(server, server_id)
            logger.info("Server enabled, performing smart sync for '%s' (id=%s)", server.serverName, server_id)
            return await self.smart_sync(server)
        except Exception as e:
            logger.error(
                "sync_by_enabled_status failed for '%s' (id=%s): %s", server.serverName, server_id, e, exc_info=True
            )
            return False

    @staticmethod
    def _extract_runtime_version(server: ExtendedMCPServer) -> str | None:
        runtime_version = (server.federationMetadata or {}).get("runtimeVersion")
        return str(runtime_version) if runtime_version is not None else None

    def _group_docs_by_entity_type(self, docs: list[Any]) -> dict[str, list[Any]]:
        grouped: dict[str, list[Any]] = {"tool": [], "resource": [], "prompt": []}
        for doc in docs:
            et = doc.metadata.get("entity_type")
            if et in grouped:
                grouped[et].append(doc)
            else:
                logger.warning("Unknown entity_type '%s' in doc metadata.", et)
        return grouped

    async def _sync_entity_type(
        self,
        entity_type: str,
        existing_docs: list[Any],
        new_docs: list[Any],
    ) -> tuple[int, int, int]:
        """Compare existing vs new docs for one entity type; return (added, deleted, updated)."""
        existing_map = self._build_doc_map(existing_docs)
        new_map = self._build_doc_map(new_docs)

        to_delete: list[str] = []
        to_add: list[Any] = []
        to_update_metadata: list[tuple[str, dict]] = []

        for key, old_doc in existing_map.items():
            if key not in new_map:
                to_delete.append(old_doc.id)
            else:
                new_doc = new_map[key]
                if old_doc.page_content != new_doc.page_content:
                    to_delete.append(old_doc.id)
                    to_add.append(new_doc)
                else:
                    old_meta = {k: old_doc.metadata.get(k) for k in ("scope", "enabled")}
                    new_meta = {k: new_doc.metadata.get(k) for k in ("scope", "enabled")}
                    if old_meta != new_meta:
                        to_update_metadata.append((old_doc.id, new_meta))

        for key, new_doc in new_map.items():
            if key not in existing_map:
                to_add.append(new_doc)

        added = deleted = updated = 0
        if to_delete:
            self.adapter.delete(ids=to_delete, collection_name=self.collection)
            deleted = len(to_delete)
            logger.info("[%s] Deleted %d docs", entity_type, deleted)
        if to_add:
            ids = self.adapter.add_documents(documents=to_add, collection_name=self.collection)
            added = len(ids) if ids else 0
            logger.info("[%s] Added %d docs", entity_type, added)
        if to_update_metadata:
            for doc_id, meta in to_update_metadata:
                if hasattr(self.adapter, "update_metadata"):
                    self.adapter.update_metadata(doc_id=doc_id, metadata=meta, collection_name=self.collection)
            updated = len(to_update_metadata)
            logger.info("[%s] Updated metadata for %d docs", entity_type, updated)
        if not to_delete and not to_add and not to_update_metadata:
            logger.debug("[%s] No changes detected", entity_type)

        return added, deleted, updated

    async def _update_metadata_by_entity_type(self, server: ExtendedMCPServer, server_id: str) -> bool:
        """Patch enabled/status metadata per entity type — no re-embedding."""
        new_metadata = {
            "enabled": server.config.get("enabled", False) if server.config else False,
        }
        total_success = total_count = 0
        try:
            for entity_type in ServerEntityType:
                existing_docs = self.adapter.filter_by_metadata(
                    filters={"server_id": server_id, "entity_type": entity_type.value},
                    limit=1000,
                    collection_name=self.collection,
                )
                if not existing_docs:
                    continue
                success = 0
                for doc in existing_docs:
                    total_count += 1
                    if hasattr(self.adapter, "update_metadata"):
                        ok = self.adapter.update_metadata(
                            doc_id=doc.id, metadata=new_metadata, collection_name=self.collection
                        )
                        if ok:
                            success += 1
                            total_success += 1
                logger.info("[%s] Updated metadata for %d/%d docs", entity_type.value, success, len(existing_docs))
            logger.info("Metadata update complete: %d/%d docs for server_id=%s", total_success, total_count, server_id)
            return total_success == total_count
        except Exception as e:
            logger.error("_update_metadata_by_entity_type failed for server_id=%s: %s", server_id, e, exc_info=True)
            return False

    def _build_doc_map(self, docs: list[Document]) -> dict[tuple[str, Any], Document]:
        """Build lookup map keyed by (entity_type, entity_name)."""
        doc_map: dict[tuple[str, Any], Document] = {}
        for doc in docs:
            et = doc.metadata.get("entity_type")
            if et == "tool":
                key = ("tool", doc.metadata.get("tool_name"))
            elif et == "resource":
                key = ("resource", doc.metadata.get("resource_name"))
            elif et == "prompt":
                key = ("prompt", doc.metadata.get("prompt_name"))
            else:
                logger.warning("Unknown entity_type '%s', skipping doc.", et)
                continue
            doc_map[key] = doc
        return doc_map
