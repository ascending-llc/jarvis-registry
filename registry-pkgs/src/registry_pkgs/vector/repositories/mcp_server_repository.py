from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from langchain_core.documents import Document

from ...models import ExtendedMCPServer
from ...models.federation_metadata import extract_runtime_version
from ..base_sync_repository import BaseVectorSyncRepository
from ..client import DatabaseClient
from ..sync_result import VectorSyncResult

logger = logging.getLogger(__name__)


class MCPServerRepository(BaseVectorSyncRepository[ExtendedMCPServer]):
    """Vector sync repository for MCP servers."""

    ENTITY_ID_PROPERTY: ClassVar[str] = "server_id"

    FILTERABLE_PROPERTIES = {
        "server_id": "text",
        "federation_id": "text",
        "runtimeArn": "text",
        "path": "text",
        "enabled": "bool",
        "tool_enabled": "bool",
        # Declared as TEXT explicitly: input_schema holds a JSON-encoded tool parameter
        # schema. Storing it as text bypasses Weaviate's nested-property validation
        # (which rejects reserved keys like `id`/`vector` and non-GraphQL names like
        # `$ref` that JSON Schema bodies routinely contain). Old collections where this
        # property was auto-inferred as `object` must be dropped + recreated
        # (`vector_sync.py --target mcp --clean`) for new inserts to succeed.
        "input_schema": "text",
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
        """Rebuild an MCP server's docs after content changes or detected index drift.

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
            verified = False
            try:
                landed_docs = await self.afilter(filters={"server_id": server_id}, limit=expected)
                verified = len(landed_docs) >= expected
            except Exception as e:
                logger.warning(
                    "Post-insert verification failed for server '%s' (server_id=%s): %s — trusting asave return value",
                    server.serverName,
                    server_id,
                    e,
                )
                verified = True

            if verified:
                result.indexed = len(doc_ids)
                result.version = extract_runtime_version(server.federationMetadata)
                logger.info(
                    "Indexed %d docs for server '%s' (server_id=%s).",
                    result.indexed,
                    server.serverName,
                    server_id,
                )
            else:
                result.indexed = 0
                result.failed = expected
                result.error = (
                    f"asave returned {len(doc_ids)} UUIDs but fewer than {expected} docs are queryable "
                    "in Weaviate (check langchain-weaviate ERROR logs for batch insert failures)"
                )
                logger.error(
                    "Vector sync verification failed for server '%s' (server_id=%s): expected %d docs",
                    server.serverName,
                    server_id,
                    expected,
                )

        except Exception as e:
            logger.error("MCP vector sync failed for server %s: %s", getattr(server, "id", "?"), e, exc_info=True)
            result.failed = 1
            result.error = str(e)

        return result.to_dict_mcp()

    def _result_dict(self, result: VectorSyncResult) -> dict:
        return result.to_dict_mcp()

    async def sync_tools_enabled(
        self,
        server: ExtendedMCPServer,
        tool_names: list[str],
        *,
        reload: Callable[[], Awaitable[ExtendedMCPServer | None]] | None = None,
    ) -> dict:
        """Patch toggled tools' enabled state, rebuilding the server if their doc count differs."""
        if not tool_names:
            return self._result_dict(VectorSyncResult())

        names = set(tool_names)
        filters = {self.ENTITY_ID_PROPERTY: str(server.id), "tool_name": list(tool_names)}
        expected = sum(doc.metadata.get("tool_name") in names for doc in server.to_documents())

        def patch(docs: list[Document]) -> VectorSyncResult:
            groups: dict[bool, list[Document]] = {False: [], True: []}
            for doc in docs:
                enabled = not server.is_tool_disabled(doc.metadata["tool_name"])
                groups[enabled].append(doc)

            result = VectorSyncResult()
            for enabled, group in groups.items():
                if not group:
                    continue
                outcome = self._patch_docs(group, {"tool_enabled": enabled}, filters)
                result.metadata_updated += outcome.metadata_updated
                if result.error is None:
                    result.error = outcome.error
            return result

        return await self._run_metadata_sync(
            str(server.id),
            lambda: self._patch_or_rebuild(server, filters=filters, expected=expected, patch=patch, reload=reload),
        )

    async def update_tools_metadata(
        self,
        server_id: str,
        tool_names: list[str],
        metadata: dict[str, Any],
    ) -> VectorSyncResult:
        """Patch metadata on the Weaviate docs for a set of tools on one server — no re-embedding.

        Applies one metadata dict to every doc whose tool_name is in `tool_names`, in a single
        filter+batch round-trip. No-ops (empty result) when `tool_names` is empty.
        """
        if not tool_names:
            return VectorSyncResult()
        # Adapter maps a list value to contains_any (len>1) or equal (len==1); empty is guarded above.
        return await self._run_metadata_sync(
            str(server_id),
            lambda: self._update_metadata_by_filters({"server_id": server_id, "tool_name": list(tool_names)}, metadata),
        )

    async def delete_by_entity_id(self, entity_id: str, entity_name: str | None = None) -> int:
        """Remove all weaviate docs for an MCP server."""
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
