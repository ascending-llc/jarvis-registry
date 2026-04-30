from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from .protocols import VectorStorable
from .repository import Repository
from .sync_result import VectorSyncResult

T = TypeVar("T", bound=VectorStorable)

logger = logging.getLogger(__name__)


class BaseVectorSyncRepository(Repository[T], ABC):
    """Shared sync infrastructure for A2A and MCP vector repositories.

    Concrete subclasses must implement:
        sync_to_vector_db(entity, *, is_delete) -> dict
        delete_by_entity_id(entity_id, entity_name) -> int
    """

    async def ensure_collection(self) -> bool:
        """Ensure the backing Weaviate collection exists."""
        return await self._ensure_collection()

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
                limit=10000,
                collection_name=self.collection,
            )
            if not existing_docs:
                logger.debug(
                    "No Weaviate docs found for %s=%s during metadata-only update, skipping.",
                    entity_id_field,
                    entity_id,
                )
                return result

            success = 0
            for doc in existing_docs:
                ok = self.adapter.update_metadata(
                    doc_id=doc.id,
                    metadata=metadata,
                    collection_name=self.collection,
                )
                if ok:
                    success += 1

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
    def _compute_content_hash(entity: Any) -> str:
        """Stable SHA-256 of page_content only — no metadata, no timestamps.

        Sorting the page_content list ensures the hash is independent of
        the order in which to_documents() returns documents.
        """
        docs = entity.to_documents()
        contents = sorted(doc.page_content for doc in docs)
        fingerprint = "\n---\n".join(contents)
        return hashlib.sha256(fingerprint.encode()).hexdigest()

    def _vector_docs_exist(self, entity_id_field: str, entity_id: str) -> bool:
        """Lightweight existence check — metadata filter only, no token cost.

        Used after a hash match (Case 5) to detect silently-deleted Weaviate docs.
        """
        if not self._collection_has_property(entity_id_field):
            return False
        docs = self.adapter.filter_by_metadata(
            filters={entity_id_field: entity_id},
            limit=1,
            collection_name=self.collection,
        )
        return bool(docs)

    @abstractmethod
    async def sync_to_vector_db(self, entity: Any, *, is_delete: bool = True) -> dict:
        """Hash-gated full rebuild.

        Args:
            entity: The A2AAgent or ExtendedMCPServer instance.
            is_delete: True (CRUD path) — delete existing docs before reinserting.
                       False (federation path) — external caller already deleted; insert only.
        """
        ...

    @abstractmethod
    async def delete_by_entity_id(self, entity_id: str, entity_name: str | None = None) -> int:
        """Remove all Weaviate docs for the given entity MongoDB ID."""
        ...
