from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from .protocols import VectorStorable
from .repository import Repository
from .sync_result import VectorSyncResult

T = TypeVar("T", bound=VectorStorable)

logger = logging.getLogger(__name__)

_MAX_METADATA_DOCS = 10_000


class BaseVectorSyncRepository(Repository[T], ABC):
    """Shared sync infrastructure for A2A and MCP vector repositories.

    Concrete subclasses must implement:
        sync_to_vector_db(entity, *, is_delete) -> dict
        delete_by_entity_id(entity_id, entity_name) -> int
    """

    async def ensure_collection(self) -> bool:
        """Ensure the backing Weaviate collection exists and has all required filterable properties."""
        result = await self._ensure_collection()
        filterable = getattr(self, "FILTERABLE_PROPERTIES", [])
        if filterable:
            self.adapter.ensure_filterable_properties(self.collection, filterable)
        return result

    async def delete_by_runtime_identity(self, federation_id: str, runtime_arn: str) -> int:
        """Delete all Weaviate docs that match a federation identity.

        Called by federation_sync_service before reinserting updated docs.
        """
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
        """Patch metadata fields on all docs for an entity — no re-embedding.

        Used for toggle/status-only changes where page_content is unchanged.
        Falls back silently when the adapter does not support update_metadata.
        """
        result = VectorSyncResult()
        if not await self.ensure_collection():
            raise RuntimeError(f"collection '{self.collection}' is not initialized")
        try:
            if not hasattr(self.adapter, "update_metadata"):
                logger.warning(
                    "Adapter does not support update_metadata; metadata-only update skipped for %s=%s.",
                    entity_id_field,
                    entity_id,
                )
                return result

            existing_docs = self.adapter.filter_by_metadata(
                filters={entity_id_field: entity_id},
                limit=_MAX_METADATA_DOCS,
                collection_name=self.collection,
            )
            if not existing_docs:
                logger.debug(
                    "No Weaviate docs found for %s=%s during metadata-only update, skipping.",
                    entity_id_field,
                    entity_id,
                )
                return result

            doc_ids = [doc.id for doc in existing_docs]
            if hasattr(self.adapter, "batch_update_properties"):
                success = self.adapter.batch_update_properties(
                    doc_ids=doc_ids,
                    update_data=metadata,
                    collection_name=self.collection,
                )
            else:
                success = sum(
                    1
                    for doc_id in doc_ids
                    if self.adapter.update_metadata(
                        doc_id=doc_id,
                        metadata=metadata,
                        collection_name=self.collection,
                    )
                )

            result.metadata_updated = success
            logger.info(
                "Metadata-only update: %d/%d docs updated for %s=%s",
                success,
                len(existing_docs),
                entity_id_field,
                entity_id,
            )
        except Exception as e:
            logger.error(
                "Metadata-only update failed for %s=%s: %s",
                entity_id_field,
                entity_id,
                e,
                exc_info=True,
            )
            result.error = str(e)
        return result

    @staticmethod
    def _extract_runtime_version(
        federation_metadata: dict | None,
        fallback_keys: tuple[str, ...] = ("runtimeVersion",),
    ) -> str | None:
        """Extract a runtime/agent version string from federationMetadata.

        Tries each key in *fallback_keys* in order and returns the first non-None value.
        """
        for key in fallback_keys:
            value = (federation_metadata or {}).get(key)
            if value is not None:
                return str(value)
        return None

    @abstractmethod
    async def sync_to_vector_db(self, entity: Any, *, is_delete: bool = True) -> VectorSyncResult:
        """Full rebuild — call only when content has changed.

        Args:
            entity: The A2AAgent or ExtendedMCPServer instance.
            is_delete: True (CRUD path) — delete existing docs before reinserting.
                       False (federation path) — external caller already deleted; insert only.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_by_entity_id(self, entity_id: str, entity_name: str | None = None) -> int:
        """Remove all Weaviate docs for the given entity MongoDB ID."""
        raise NotImplementedError
