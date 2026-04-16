"""
MCP Server Specialized Repository

Contains MCP Server-specific vector database operations
that don't belong in the generic Repository class.
"""

import logging
from typing import Any

from langchain_core.documents import Document

from ...models import ExtendedMCPServer
from ...models.enums import ServerEntityType
from ..client import DatabaseClient
from ..repository import Repository

logger = logging.getLogger(__name__)


class MCPServerRepository(Repository[ExtendedMCPServer]):
    """
    Specialized repository for MCP Server operations.

    Extends generic Repository with MCP-specific methods like
    sync_server_to_vector_db() that shouldn't be in the base class.
    """

    VECTOR_SCAN_BATCH_SIZE = 500

    def __init__(self, db_client: DatabaseClient):
        """
        Initialize MCP Server repository.

        Args:
            db_client: Database client instance
        """
        super().__init__(db_client, ExtendedMCPServer)
        logger.info("MCPServerRepository initialized")

    @staticmethod
    def _extract_runtime_version(server: ExtendedMCPServer) -> str | None:
        runtime_version = (server.federationMetadata or {}).get("runtimeVersion")
        if runtime_version is None:
            return None
        return str(runtime_version)

    def _load_existing_docs(self, server_id: str) -> list[Document]:
        if not self._collection_has_property("server_id"):
            return []
        docs = self._load_docs_by_metadata_paginated(
            filters={"server_id": server_id},
            batch_size=self.VECTOR_SCAN_BATCH_SIZE,
        )

        if docs:
            logger.debug(
                "Loaded %d existing MCP vector docs for server_id=%s using paginated metadata scan.",
                len(docs),
                server_id,
            )
        return docs

    def _runtime_version_property_name(self) -> str | None:
        if self._collection_has_property("runtimeVersion"):
            return "runtimeVersion"
        return None

    def _should_skip_reindex(self, server: ExtendedMCPServer, server_id: str) -> tuple[bool, str | None]:
        current_version = self._extract_runtime_version(server)
        if not current_version:
            return False, None
        version_property = self._runtime_version_property_name()
        if not version_property:
            return False, current_version

        existing_docs = self._load_existing_docs(server_id)
        if not existing_docs:
            return False, current_version

        expected_docs = server.to_documents()
        existing_versions = {
            str(doc.metadata.get(version_property))
            for doc in existing_docs
            if doc.metadata.get(version_property) is not None
        }

        if len(existing_docs) != len(expected_docs):
            logger.info(
                "Rebuild MCP vector docs for server '%s' (server_id=%s) because document count changed: mongo=%d weaviate=%d",
                server.serverName,
                server_id,
                len(expected_docs),
                len(existing_docs),
            )
            return False, current_version

        if existing_versions == {current_version}:
            logger.info(
                "Skip MCP vector rebuild for server '%s' (server_id=%s) because runtime_version=%s already matches Weaviate.",
                server.serverName,
                server_id,
                current_version,
            )
            return True, current_version

        logger.info(
            "Rebuild MCP vector docs for server '%s' (server_id=%s) because runtime_version differs: mongo=%s weaviate=%s",
            server.serverName,
            server_id,
            current_version,
            sorted(existing_versions) if existing_versions else [],
        )
        return False, current_version

    async def ensure_collection(self) -> bool:
        """
        Ensure the collection exists in vector database.
        """
        return await self._ensure_collection()

    async def sync_server_to_vector_db(
        self,
        server: ExtendedMCPServer,
        is_delete: bool = True,
    ) -> dict[str, int | str | None]:
        """
        Full rebuild: delete old server and recreate from server object.

        Uses server_id as primary identifier if available, falls back to path.
        This method is mainly used for initial sync from MongoDB to Weaviate.

        Args:
            server: ExtendedMCPServer object from MongoDB
            is_delete: Whether to delete old server records before saving

        Returns:
            {"indexed_tools": count, "failed_tools": count, "deleted": count}
        """
        try:
            collection_existed = self.adapter.collection_exists(self.collection)
            await self.ensure_collection()

            # 1. Extract identifiers from server object
            server_id = str(server.id) if server.id else None
            server_name = server.serverName

            # 2. Delete old server records if requested
            deleted = 0
            skipped = 0
            if is_delete and server_id:
                if not collection_existed:
                    logger.info(
                        "Collection '%s' did not exist before sync. Skip delete step for server_id=%s.",
                        self.collection,
                        server_id,
                    )
                elif not self._collection_has_property("server_id"):
                    logger.info(
                        "Collection '%s' schema has no 'server_id' property. Skip delete step for server_id=%s.",
                        self.collection,
                        server_id,
                    )
                else:
                    deleted = await self.adelete_by_filter({"server_id": server_id})
                    if deleted > 0:
                        logger.info(f"Deleted {deleted} old record(s) by server_id: {server_id}")
            elif not is_delete and server_id:
                should_skip, current_version = self._should_skip_reindex(server, server_id)
                if should_skip:
                    skipped = 1
                    return {
                        "indexed_tools": 0,
                        "failed_tools": 0,
                        "deleted": 0,
                        "skipped": skipped,
                        "version": current_version,
                        "error": None,
                    }

            # 3. Save server object to vector database
            doc_id = await self.asave(server)
            success = doc_id is not None

            logger.info(f"Indexed server '{server_name}' (server_id: {server_id}):{'success' if success else 'failed'}")
            return {
                "indexed_tools": 1 if success else 0,
                "failed_tools": 0 if success else 1,
                "deleted": deleted,
                "skipped": skipped,
                "version": self._extract_runtime_version(server),
                "error": None,
            }

        except Exception as e:
            logger.error(f"Full sync failed for server {server.serverName}: {e}", exc_info=True)
            return {
                "indexed_tools": 0,
                "failed_tools": 1,
                "deleted": 0,
                "skipped": 0,
                "version": None,
                "error": str(e),
            }

    async def get_by_server_id(self, server_id: str) -> ExtendedMCPServer | None:
        """
        Get server by MongoDB server_id (stored in metadata).

        Args:
            server_id: MongoDB _id as string

        Returns:
            ExtendedMCPServer instance if found, None otherwise
        """
        try:
            results = await self.afilter(filters={"server_id": server_id}, limit=1)
            return results[0] if results else None
        except Exception as e:
            logger.error(f"Get by server_id failed: {e}")
            return None

    async def delete_by_runtime_identity(self, federation_id: str, runtime_arn: str) -> int:
        """Delete one federated MCP runtime slice from Weaviate by federation + runtime."""
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
        """Return whether Weaviate already contains docs for one federated MCP runtime."""
        if not self._collection_has_property("federation_id") or not self._collection_has_property("runtimeArn"):
            return False
        docs = self.adapter.filter_by_metadata(
            filters={"federation_id": federation_id, "runtimeArn": runtime_arn},
            limit=1,
            collection_name=self.collection,
        )
        return bool(docs)

    async def get_all_docs_by_server_id(self, server_id: str) -> dict[str, list[Any]]:
        """
        Get all vector documents (tools, resources, prompts) by server_id.

        Query by entity type separately for better traceability.

        Returns:
            Dict with keys: 'tools', 'resources', 'prompts'
            Each value is a list of LangChain Documents from weaviate
        """
        try:
            # P2-2: include server_summary bucket
            result: dict[str, list[Any]] = {
                "tools": [],
                "resources": [],
                "prompts": [],
                "server_summaries": [],
            }

            # Map entity_type value → result key
            _key_map = {
                "tool": "tools",
                "resource": "resources",
                "prompt": "prompts",
                "server_summary": "server_summaries",
            }

            # Query each entity type separately for better logging and debugging
            for entity_type in ServerEntityType:
                entity_type_value = entity_type.value
                result_key = _key_map.get(entity_type_value, f"{entity_type_value}s")

                logger.debug(f"Querying {entity_type_value} docs for server_id {server_id}")

                docs = self.adapter.filter_by_metadata(
                    filters={"server_id": server_id, "entity_type": entity_type_value},
                    limit=1000,
                    collection_name=self.collection,
                )

                result[result_key] = docs

                logger.debug(f"Found {len(docs)} {entity_type_value} docs for server_id {server_id}")

            logger.info(
                f"Retrieved docs for server_id {server_id}: "
                f"tools={len(result['tools'])}, resources={len(result['resources'])}, "
                f"prompts={len(result['prompts'])}, server_summaries={len(result['server_summaries'])}"
            )
            return result

        except Exception as e:
            logger.error(f"Get all docs by server_id failed: {e}", exc_info=True)
            return {"tools": [], "resources": [], "prompts": []}

    async def smart_sync(
        self,
        server: ExtendedMCPServer,
    ) -> bool:
        """
        Smart incremental sync with fine-grained comparison by entity type.

        Strategy:
        1. Generate new documents from server (grouped by entity type)
        2. For each entity type (TOOL, RESOURCE, PROMPT):
           - Query existing docs in Weaviate
           - Compare with new docs
           - Determine if content changed or only metadata changed
           - Update accordingly (delete+add for content changes, update for metadata only)

        Args:
            server: Server instance from MongoDB

        Returns:
            True if sync successful, False otherwise
        """
        await self.ensure_collection()

        server_id = str(server.id)
        server_name = server.serverName

        try:
            new_docs = server.to_documents()
            new_docs_by_type = self._group_docs_by_entity_type(new_docs)

            # Track overall changes
            total_added = 0
            total_deleted = 0
            total_updated = 0

            # Process each entity type separately (includes SERVER_SUMMARY after P2-1/P2-2)
            for entity_type in ServerEntityType:
                entity_type_value = entity_type.value
                logger.debug(f"Processing entity type: {entity_type_value} for server {server_name}")

                # Step 1: Query existing docs for this entity type
                existing_docs = self.adapter.filter_by_metadata(
                    filters={"server_id": server_id, "entity_type": entity_type_value},
                    limit=10000,  # Generous limit
                    collection_name=self.collection,
                )

                # Step 2: Get new docs for this entity type
                new_docs_for_type = new_docs_by_type.get(entity_type_value, [])

                # Step 3: Compare and sync
                if not existing_docs and not new_docs_for_type:
                    # No existing, no new: skip
                    logger.debug(f"No docs for {entity_type_value}, skipping")
                    continue

                elif not existing_docs and new_docs_for_type:
                    # No existing, has new: add all
                    logger.info(f"Adding {len(new_docs_for_type)} new {entity_type_value} docs")
                    new_ids = self.adapter.add_documents(documents=new_docs_for_type, collection_name=self.collection)
                    total_added += len(new_ids) if new_ids else 0

                elif existing_docs and not new_docs_for_type:
                    # Has existing, no new: delete all
                    logger.info(f"Deleting {len(existing_docs)} removed {entity_type_value} docs")
                    doc_ids_to_delete = [doc.id for doc in existing_docs]
                    self.adapter.delete(ids=doc_ids_to_delete, collection_name=self.collection)
                    total_deleted += len(doc_ids_to_delete)

                else:
                    # Both exist: compare and update
                    added, deleted, updated = await self._sync_entity_type(
                        entity_type_value,
                        existing_docs,
                        new_docs_for_type,
                    )
                    total_added += added
                    total_deleted += deleted
                    total_updated += updated

            logger.info(
                f"Smart sync completed for '{server_name}': "
                f"added={total_added}, deleted={total_deleted}, updated={total_updated}"
            )
            return True

        except Exception as e:
            logger.error(f"Smart sync failed for '{server_name}' (ID: {server_id}): {e}", exc_info=True)
            return False

    def _group_docs_by_entity_type(self, docs: list[Any]) -> dict[str, list[Any]]:
        """
        Group documents by entity_type.

        Args:
            docs: List of LangChain Documents

        Returns:
            Dict mapping entity_type to list of documents
        """
        grouped: dict[str, list[Any]] = {
            "tool": [],
            "resource": [],
            "prompt": [],
            "server_summary": [],  # P2-2
        }

        for doc in docs:
            entity_type = doc.metadata.get("entity_type")
            if entity_type in grouped:
                grouped[entity_type].append(doc)
            else:
                logger.warning(f"Unknown entity_type: {entity_type}")

        return grouped

    async def _sync_entity_type(
        self,
        entity_type: str,
        existing_docs: list[Any],
        new_docs: list[Any],
    ) -> tuple[int, int, int]:
        """
        Sync a specific entity type by comparing existing and new docs.

        Args:
            entity_type: Entity type (tool, resource, prompt)
            existing_docs: Existing documents from Weaviate
            new_docs: New documents to sync

        Returns:
            Tuple of (added_count, deleted_count, updated_count)
        """
        # Build lookup maps
        existing_map = self.build_doc_map(existing_docs)
        new_map = self.build_doc_map(new_docs)

        to_delete = []
        to_add = []
        to_update_metadata = []

        # Compare existing with new
        for key, old_doc in existing_map.items():
            if key not in new_map:
                # Document removed
                to_delete.append(old_doc.id)
                logger.debug(f"[{entity_type}] Document removed: {key}")
            else:
                new_doc = new_map[key]

                # Check if content changed
                if old_doc.page_content != new_doc.page_content:
                    # Content changed: delete old and add new
                    to_delete.append(old_doc.id)
                    to_add.append(new_doc)
                    logger.debug(f"[{entity_type}] Content changed for {key}, will re-register")
                else:
                    # Content unchanged, check metadata
                    old_meta = {k: v for k, v in old_doc.metadata.items() if k in ["scope", "enabled"]}
                    new_meta = {k: v for k, v in new_doc.metadata.items() if k in ["scope", "enabled"]}

                    if old_meta != new_meta:
                        to_update_metadata.append((old_doc.id, new_meta))
                        logger.debug(f"[{entity_type}] Metadata changed for {key}, will update")

        # Check for new documents
        for key, new_doc in new_map.items():
            if key not in existing_map:
                to_add.append(new_doc)
                logger.debug(f"[{entity_type}] New document: {key}")

        # Execute updates
        added_count = 0
        deleted_count = 0
        updated_count = 0

        if to_delete:
            self.adapter.delete(ids=to_delete, collection_name=self.collection)
            deleted_count = len(to_delete)
            logger.info(f"[{entity_type}] Deleted {deleted_count} documents")

        if to_add:
            new_ids = self.adapter.add_documents(documents=to_add, collection_name=self.collection)
            added_count = len(new_ids) if new_ids else 0
            logger.info(f"[{entity_type}] Added {added_count} documents")

        if to_update_metadata:
            for doc_id, metadata in to_update_metadata:
                if hasattr(self.adapter, "update_metadata"):
                    self.adapter.update_metadata(doc_id=doc_id, metadata=metadata, collection_name=self.collection)
            updated_count = len(to_update_metadata)
            logger.info(f"[{entity_type}] Updated metadata for {updated_count} documents")

        if not to_delete and not to_add and not to_update_metadata:
            logger.debug(f"[{entity_type}] No changes detected")

        return added_count, deleted_count, updated_count

    def build_doc_map(self, docs: list[Document]) -> dict[str, Document]:
        """
        Build a lookup map for documents using entity_type and entity name as key.

        Args:
            docs: List of LangChain Documents

        Returns:
            Dict mapping (entity_type, entity_name) to document
        """
        doc_map = {}
        for doc in docs:
            entity_type = doc.metadata.get("entity_type")

            # Determine unique key based on entity type
            if entity_type == "tool":
                key = ("tool", doc.metadata.get("tool_name"))
            elif entity_type == "resource":
                key = ("resource", doc.metadata.get("resource_name"))
            elif entity_type == "prompt":
                key = ("prompt", doc.metadata.get("prompt_name"))
            elif entity_type == "server_summary":
                # P2-2: one summary doc per server; keyed by server_name
                key = ("server_summary", doc.metadata.get("server_name"))
            else:
                logger.warning(f"Unknown entity_type: {entity_type}")
                continue

            doc_map[key] = doc

        return doc_map

    async def _update_metadata_only(self, server: ExtendedMCPServer, server_id: str) -> bool:
        """
        Update only metadata for all existing docs (no re-vectorization).

        Query and update by entity type separately for better traceability.

        Args:
            server: Server instance with new metadata
            server_id: MongoDB server ID

        Returns:
            True if all updates successful
        """
        try:
            new_metadata = {
                "enabled": server.config.get("enabled", False) if server.config else False,
            }

            total_success = 0
            total_count = 0

            # Update each entity type separately for better logging
            for entity_type in ServerEntityType:
                entity_type_value = entity_type.value

                logger.debug(f"Updating metadata for {entity_type_value} docs (server_id: {server_id})")

                # Query existing docs for this entity type
                existing_docs = self.adapter.filter_by_metadata(
                    filters={"server_id": server_id, "entity_type": entity_type_value},
                    limit=1000,
                    collection_name=self.collection,
                )

                if not existing_docs:
                    logger.debug(f"No {entity_type_value} docs found, skipping")
                    continue

                # Update metadata for each doc
                success_count = 0
                for doc in existing_docs:
                    total_count += 1
                    if hasattr(self.adapter, "update_metadata"):
                        result = self.adapter.update_metadata(
                            doc_id=doc.id, metadata=new_metadata, collection_name=self.collection
                        )
                        if result:
                            success_count += 1
                            total_success += 1

                logger.info(f"[{entity_type_value}] Updated metadata for {success_count}/{len(existing_docs)} docs")

            logger.info(f"Total: Updated metadata for {total_success}/{total_count} docs")
            return total_success == total_count

        except Exception as e:
            logger.error(f"Metadata-only update failed: {e}", exc_info=True)
            return False

    async def sync_by_enabled_status(
        self,
        server: ExtendedMCPServer,
        enabled: bool,
    ) -> bool:
        """
        Sync server to vector DB based on enabled status.

        - If enabled=False: Only update metadata (set enabled=false) for existing docs
        - If enabled=True: Perform smart sync (full content update)

        Updates by entity type separately for better traceability.

        Args:
            server: Server instance
            enabled: Whether server is enabled

        Returns:
            True if sync successful, False otherwise
        """
        await self.ensure_collection()
        server_id = str(server.id)
        server_name = server.serverName

        try:
            if not enabled:
                logger.info(f"Server disabled, updating metadata for '{server_name}' (ID: {server_id})")

                has_docs = False
                for entity_type in ServerEntityType:
                    docs = self.adapter.filter_by_metadata(
                        filters={"server_id": server_id, "entity_type": entity_type.value},
                        limit=1000,
                        collection_name=self.collection,
                    )
                    if docs:
                        has_docs = True
                        break

                if not has_docs:
                    logger.debug(f"No existing docs for '{server_name}', nothing to update")
                    return True
                return await self._update_metadata_only(server, server_id)
            else:
                # Server enabled: perform smart sync (full content update)
                logger.info(f"Server enabled, performing smart sync for '{server_name}' (ID: {server_id})")
                return await self.smart_sync(server)

        except Exception as e:
            logger.error(f"Sync by enabled status failed for '{server_name}' (ID: {server_id}): {e}", exc_info=True)
            return False

    async def delete_by_server_id(self, server_id: str, server_name: str | None = None) -> bool:
        """
        Delete server from vector DB by MongoDB server ID.

        Delete by entity type separately for better traceability.

        Args:
            server_id: MongoDB server ID
            server_name: Server name (optional, for better logging)

        Returns:
            True if deletion successful, False otherwise
        """
        await self.ensure_collection()
        log_name = f"'{server_name}' (ID: {server_id})" if server_name else f"ID: {server_id}"

        try:
            total_deleted = 0

            # Delete each entity type separately for better logging
            for entity_type in ServerEntityType:
                entity_type_value = entity_type.value

                logger.debug(f"Deleting {entity_type_value} docs for server_id {server_id}")

                # Query docs for this entity type
                docs = self.adapter.filter_by_metadata(
                    filters={"server_id": server_id, "entity_type": entity_type_value},
                    limit=1000,
                    collection_name=self.collection,
                )

                if docs:
                    doc_ids = [doc.id for doc in docs]
                    self.adapter.delete(ids=doc_ids, collection_name=self.collection)
                    total_deleted += len(doc_ids)
                    logger.info(f"[{entity_type_value}] Deleted {len(doc_ids)} documents")
                else:
                    logger.debug(f"[{entity_type_value}] No documents found")

            if total_deleted > 0:
                logger.info(f"Successfully removed server {log_name} from vector DB (deleted {total_deleted} records)")
                return True
            else:
                logger.debug(f"Server {log_name} not found in vector DB")
                return True

        except Exception as e:
            logger.error(f"Failed to remove server {log_name} from vector DB: {e}", exc_info=True)
            return False
