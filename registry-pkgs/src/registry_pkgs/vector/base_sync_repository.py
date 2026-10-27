from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Protocol, TypeVar

from langchain_core.documents import Document

from .protocols import VectorStorable
from .repository import Repository
from .sync_result import VectorSyncResult


class _SyncEntity(VectorStorable, Protocol):
    vectorContentHash: str | None

    def to_documents(self) -> list[Document]: ...


T = TypeVar("T", bound=_SyncEntity)

logger = logging.getLogger(__name__)

_MAX_METADATA_DOCS = 10_000


class BaseVectorSyncRepository(Repository[T], ABC):
    """Shared sync infrastructure for A2A and MCP vector repositories.

    Concrete subclasses must implement:
        sync_to_vector_db(entity, *, is_delete) -> dict
        delete_by_entity_id(entity_id, entity_name) -> int
    """

    ENTITY_ID_PROPERTY: ClassVar[str]

    async def ensure_collection(self) -> bool:
        """Ensure the backing Weaviate collection exists and has all required filterable properties."""
        result = await self._ensure_collection()
        filterable = getattr(self, "FILTERABLE_PROPERTIES", [])
        if filterable:
            self._write_adapter.ensure_filterable_properties(self.collection, filterable)
        return result

    async def reset_collection(self) -> None:
        """Drop the backing collection and recreate it with all required filterable properties."""
        if not hasattr(self.adapter, "drop_collection"):
            raise RuntimeError(f"Adapter {type(self.adapter).__name__} does not support drop_collection")
        self._write_adapter.drop_collection(self.collection)
        await self.ensure_collection()
        logger.info("Reset collection '%s'", self.collection)

    async def delete_by_runtime_identity(self, federation_id: str, runtime_arn: str) -> int:
        """Delete all docs matching a federation identity (used before reinserting updated docs)."""
        if not self._collection_has_property("federation_id") or not self._collection_has_property("runtimeArn"):
            logger.info(
                "Collection '%s' missing federation_id/runtimeArn property, skipping delete.",
                self.collection,
            )
            return 0
        return await self.adelete_by_filter({"federation_id": federation_id, "runtimeArn": runtime_arn})

    def has_runtime_identity(self, federation_id: str, runtime_arn: str) -> bool:
        """Return True if any Weaviate doc exists for the given federation identity."""
        if not self._collection_has_property("federation_id") or not self._collection_has_property("runtimeArn"):
            return False
        docs = self.adapter.filter_by_metadata(
            filters={"federation_id": federation_id, "runtimeArn": runtime_arn},
            limit=1,
            collection_name=self.collection,
        )
        return bool(docs)

    async def update_entity_metadata(
        self,
        entity_id_field: str,
        entity_id: str,
        metadata: dict[str, Any],
    ) -> VectorSyncResult:
        """Patch metadata on all docs for an entity — no re-embedding (toggle/status-only changes)."""
        return await self._update_metadata_by_filters({entity_id_field: entity_id}, metadata)

    async def _update_metadata_by_filters(
        self,
        filters: dict[str, Any],
        metadata: dict[str, Any],
    ) -> VectorSyncResult:
        """Patch metadata on all docs matching an arbitrary filter dict — no re-embedding."""
        result = VectorSyncResult()
        if not await self.ensure_collection():
            raise RuntimeError(f"collection '{self.collection}' is not initialized")
        try:
            if not hasattr(self.adapter, "update_metadata"):
                logger.warning(
                    "Adapter does not support update_metadata; metadata-only update skipped for %s.",
                    filters,
                )
                result.failed = 1
                result.error = "Adapter does not support metadata updates"
                return result

            existing_docs = await asyncio.to_thread(self._find_docs, filters)
            if not existing_docs:
                logger.debug(
                    "No Weaviate docs found for %s during metadata-only update, skipping.",
                    filters,
                )
                return result

            return await asyncio.to_thread(self._patch_docs, existing_docs, metadata, filters)
        except Exception as e:
            # Keep the plain-patch API's lookup failures in the result for script callers.
            logger.error("Metadata-only update failed for %s: %s", filters, e, exc_info=True)
            result.error = str(e)
        return result

    def _find_docs(self, filters: dict[str, Any], *, raise_on_error: bool = False) -> list[Document]:
        return self.adapter.filter_by_metadata(
            filters=filters,
            limit=_MAX_METADATA_DOCS,
            collection_name=self.collection,
            raise_on_error=raise_on_error,
        )

    def _patch_docs(self, docs: list[Document], metadata: dict[str, Any], filters: dict[str, Any]) -> VectorSyncResult:
        result = VectorSyncResult()
        try:
            doc_ids = [doc.id for doc in docs]
            if hasattr(self.adapter, "batch_update_properties"):
                success = self._write_adapter.batch_update_properties(
                    doc_ids=doc_ids,
                    update_data=metadata,
                    collection_name=self.collection,
                )
            else:
                success = sum(
                    1
                    for doc_id in doc_ids
                    if self._write_adapter.update_metadata(
                        doc_id=doc_id,
                        metadata=metadata,
                        collection_name=self.collection,
                    )
                )

            result.metadata_updated = success
            logger.info(
                "Metadata-only update: %d/%d docs updated for %s",
                success,
                len(docs),
                filters,
            )
        except Exception as e:
            logger.error(
                "Metadata-only update failed for %s: %s",
                filters,
                e,
                exc_info=True,
            )
            result.error = str(e)
        return result

    def _result_dict(self, result: VectorSyncResult) -> dict:
        return result.to_dict()

    async def _patch_or_rebuild(
        self,
        entity: T,
        *,
        filters: dict[str, Any],
        expected: int,
        patch: Callable[[list[Document]], VectorSyncResult],
        reload: Callable[[], Awaitable[T | None]] | None = None,
    ) -> dict:
        """Patch matching docs, rebuilding the snapshot only when a successful lookup finds drift."""
        if not await self.ensure_collection():
            raise RuntimeError(f"collection '{self.collection}' is not initialized")
        if not hasattr(self.adapter, "update_metadata"):
            logger.warning("Adapter does not support update_metadata; metadata-only update skipped for %s.", filters)
            return self._result_dict(VectorSyncResult())

        # A failed read is not evidence of missing docs and must never trigger embedding.
        found = await asyncio.to_thread(self._find_docs, filters, raise_on_error=True)
        if expected >= _MAX_METADATA_DOCS:
            # The lookup is capped at the limit, so excess docs beyond it can't be counted.
            logger.warning(
                "Skipping doc-count comparison for entity %s: expected=%d reaches metadata lookup limit=%d, filters=%s",
                entity.id,
                expected,
                _MAX_METADATA_DOCS,
                filters,
            )
        elif len(found) != expected:
            logger.warning(
                "Vector doc-count mismatch for entity %s: found=%d expected=%d, filters=%s",
                entity.id,
                len(found),
                expected,
                filters,
            )
            if reload is not None:
                current = await reload()
                if current is None or current.vectorContentHash != entity.vectorContentHash:
                    logger.info("Skipping vector rebuild for entity %s: entity deleted or content changed", entity.id)
                    return self._result_dict(VectorSyncResult())

            # Reload is only a guard: before commit it may still contain older metadata.
            return await self.sync_to_vector_db(entity, is_delete=True)

        if not found:
            return self._result_dict(VectorSyncResult())
        result = await asyncio.to_thread(patch, found)
        if result.metadata_updated < len(found) and result.error is None:
            result.error = f"Metadata-only update incomplete: {result.metadata_updated}/{len(found)} docs updated"
            logger.error("%s for %s", result.error, filters)
        return self._result_dict(result)

    async def sync_entity_metadata(
        self,
        entity: T,
        metadata: dict[str, Any],
        *,
        reload: Callable[[], Awaitable[T | None]] | None = None,
    ) -> dict:
        """Patch an entity's metadata, or rebuild its docs when their count differs."""
        filters = {self.ENTITY_ID_PROPERTY: str(entity.id)}
        return await self._patch_or_rebuild(
            entity,
            filters=filters,
            expected=len(entity.to_documents()),
            patch=lambda docs: self._patch_docs(docs, metadata, filters),
            reload=reload,
        )

    @abstractmethod
    async def sync_to_vector_db(self, entity: T, *, is_delete: bool = True) -> dict:
        """Re-embed and rebuild an entity's docs after content changes or detected index drift.

        ``is_delete``: True (CRUD) deletes existing docs before reinserting; False (federation) inserts
        only, since the caller already deleted.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_by_entity_id(self, entity_id: str, entity_name: str | None = None) -> int:
        """Remove all Weaviate docs for the given entity MongoDB ID."""
        raise NotImplementedError
