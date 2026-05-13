import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from langchain_classic.retrievers.contextual_compression import ContextualCompressionRetriever
from langchain_core.documents import Document

from .batch_result import BatchResult
from .client import DatabaseClient
from .enum.enums import RerankerProvider, SearchType
from .exceptions import RepositoryError
from .protocols import VectorStorable
from .retrievers.adapter_retriever import AdapterRetriever

logger = logging.getLogger(__name__)

# Type variables
T = TypeVar("T", bound=VectorStorable)
P = TypeVar("P")
R = TypeVar("R")


def async_wrapper[R](func: Callable[..., R]) -> Callable[..., Awaitable[R]]:
    """
    Decorator to auto-generate async version of sync method.

    Eliminates boilerplate async methods that just wrap sync calls with asyncio.to_thread.

    """

    @wraps(func)
    async def wrapper(*args, **kwargs) -> R:
        return await asyncio.to_thread(func, *args, **kwargs)

    wrapper.__name__ = f"a{func.__name__}"  # asave, aget, etc.
    wrapper.__doc__ = f"Async version of {func.__name__} (auto-generated)"
    return wrapper


class Repository[T: VectorStorable]:
    """
    Generic repository for vector database operations with smart update detection.

    Type-safe, ORM-style API for model operations with automatic vectorization
    and intelligent change detection to avoid unnecessary re-vectorization.

    Type Parameters:
        T: Model class implementing VectorStorable protocol
    """

    def __init__(self, db_client: DatabaseClient, model_class: type[T]):
        """
        Initialize repository with database client and model class.

        Args:
            model_class: Model class implementing VectorStorable protocol

        Raises:
            TypeError: If model_class doesn't implement VectorStorable
        """
        # Validate that model_class implements VectorStorable protocol
        if not isinstance(model_class, type):
            raise TypeError(f"model_class must be a class, got {type(model_class)}")

        # Runtime check for VectorStorable protocol
        required_attrs = ["COLLECTION_NAME", "from_document"]
        missing_attrs = [attr for attr in required_attrs if not hasattr(model_class, attr)]
        if missing_attrs:
            raise TypeError(
                f"{model_class.__name__} must implement VectorStorable protocol. Missing: {', '.join(missing_attrs)}"
            )

        self.db_client = db_client
        self.model_class = model_class
        self.collection = model_class.COLLECTION_NAME

        logger.debug(f"Repository initialized for {model_class.__name__} -> {self.collection}")

    @property
    def adapter(self):
        """Lazily resolve the underlying adapter when a vector operation runs."""
        return self.db_client.adapter

    def _collection_has_property(self, property_name: str) -> bool:
        """Check whether the backing collection exposes a given metadata property."""
        return self.adapter.has_property(self.collection, property_name)

    async def _ensure_collection(self) -> bool:
        """Ensure the backing vector collection exists before repository operations run."""
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

    def _load_docs_by_metadata_paginated(
        self,
        *,
        filters: dict[str, Any],
        batch_size: int = 500,
    ) -> list[Document]:
        """Load all matching documents by metadata using offset-based pagination."""
        docs: list[Document] = []
        offset = 0

        while True:
            batch = self.adapter.filter_by_metadata(
                filters,
                limit=batch_size,
                offset=offset,
                collection_name=self.collection,
            )
            if not batch:
                break
            docs.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size

        return docs

    # ========================================
    # CRUD Operations with Smart Update Detection
    # ========================================

    def save(self, instance: T) -> list[str] | None:
        """
        Save a model instance to vector database.

        Args:
            instance: Model instance to save

        Returns:
            Document ID if successful, None if failed

        Raises:
            RepositoryError: If save operation fails critically
        """
        try:
            # Generate documents from instance
            docs = instance.to_documents()
            logger.debug(f"Generated {len(docs)} documents for saving")

            doc_ids = self.adapter.add_documents(documents=docs, collection_name=self.collection)

            if doc_ids and len(doc_ids) > 0:
                logger.info(
                    f"Saved {len(docs)} documents for {self.model_class.__name__} (IDs: {len(doc_ids)} returned)"
                )
                return doc_ids

            logger.warning(f"Save returned empty IDs for {self.model_class.__name__}")
            return None

        except Exception as e:
            logger.error(f"Save failed for {self.model_class.__name__}: {e}", exc_info=True)
            raise RepositoryError(f"Failed to save {self.model_class.__name__}") from e

    def get(self, doc_id: str) -> T | None:
        """
        Get a model instance by ID.

        Args:
            doc_id: Document ID

        Returns:
            Model instance if found, None otherwise
        """
        try:
            docs = self.adapter.get_by_ids(ids=[doc_id], collection_name=self.collection)
            if docs and len(docs) > 0:
                data = self.model_class.from_document(docs[0])
                logger.debug(f"Retrieved {self.model_class.__name__} with ID: {doc_id}")
                return data

            logger.debug(f"{self.model_class.__name__} not found: {doc_id}")
            return None

        except Exception as e:
            logger.error(f"Get failed for ID {doc_id}: {e}")
            return None

    def delete(self, doc_id: str, is_server_id: bool = True) -> bool:
        """
        Delete model instance by ID.

        Args:
            doc_id: Document ID to delete
            is_server_id: If True, doc_id is MongoDB server_id (delete all related docs)
                          If False, doc_id is Weaviate UUID (delete single doc)

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            if is_server_id:
                # Delete all documents for this server
                docs = self.adapter.filter_by_metadata(
                    filters={"server_id": doc_id}, limit=1000, collection_name=self.collection
                )

                if not docs:
                    logger.warning(f"No documents found for server_id: {doc_id}")
                    return False

                doc_ids = [doc.id for doc in docs]
                self.adapter.delete(ids=doc_ids, collection_name=self.collection)
                logger.info(f"Deleted {len(doc_ids)} documents for server {doc_id}")
                return True
            else:
                # Delete single document by Weaviate UUID
                self.adapter.delete(ids=[doc_id], collection_name=self.collection)
                logger.debug(f"Deleted single document: {doc_id}")
                return True

        except Exception as e:
            logger.error(f"Delete failed for ID {doc_id}: {e}", exc_info=True)
            return False

    # ========================================
    # Bulk Operations
    # ========================================

    def bulk_save(self, instances: list[T]) -> BatchResult:
        """
        Bulk save model instances for better performance.

        Args:
            instances: List of model instances

        Returns:
            BatchResult with success/failure counts
        """
        if not instances:
            return BatchResult(total=0, successful=0, failed=0)

        try:
            # Generate documents from all instances
            docs = []
            for inst in instances:
                docs.extend(inst.to_documents())

            doc_ids = self.adapter.add_documents(documents=docs, collection_name=self.collection)

            successful = len(doc_ids) if doc_ids else 0
            total = len(instances)
            failed = total - successful

            logger.info(
                f"Bulk saved {successful}/{total} {self.model_class.__name__} instances "
                f"(success rate: {successful / total * 100:.1f}%)"
            )

            return BatchResult(total=total, successful=successful, failed=failed)

        except Exception as e:
            logger.error(f"Bulk save failed: {e}", exc_info=True)
            return BatchResult(total=len(instances), successful=0, failed=len(instances))

    def delete_by_filter(self, filters: Any) -> int | None:
        """
        Delete model instances by filter conditions.

        Args:
            filters: Database-specific filter object or dict
                - Dict: {"field": "value"} or {"field": {"$in": ["val1", "val2"]}}
                - Weaviate: weaviate.classes.query.Filter object

        Returns:
            Number of deleted documents, or None if operation failed
        """
        try:
            if hasattr(self.adapter, "delete_by_filter"):
                deleted = self.adapter.delete_by_filter(filters=filters, collection_name=self.collection)
                logger.info(f"Deleted {deleted} {self.model_class.__name__} instances by filter")
                return deleted
            else:
                logger.warning(f"Adapter {type(self.adapter).__name__} does not support delete_by_filter")
                return 0
        except Exception as e:
            logger.error(f"Delete by filter failed: {e}", exc_info=True)
            raise RepositoryError("Failed to delete by filter") from e

    def batch_update_by_filter(self, filters: Any, update_data: dict[str, Any], limit: int = 1000) -> int:
        """
        Batch update instances matching filter conditions.

        Intelligently detects if only metadata fields are being updated
        to avoid unnecessary re-vectorization.

        Args:
            filters: Filter conditions
            update_data: Dictionary of fields to update
            limit: Maximum number of instances to update

        Returns:
            Number of updated instances
        """
        try:
            # Check if only safe metadata fields are being updated
            # Safe metadata fields that can be updated without re-vectorization
            safe_fields = {"scope", "enabled", "tags"}
            update_fields = set(update_data.keys())

            if update_fields.issubset(safe_fields):
                # Metadata-only batch update (fast path)
                logger.info(f"Batch metadata-only update for {self.model_class.__name__}")

                if hasattr(self.adapter, "batch_update_properties"):
                    # Get matching instances to extract IDs
                    instances = self.filter(filters=filters, limit=limit)
                    if not instances:
                        logger.info("No documents found matching filters")
                        return 0

                    doc_ids = [str(inst.id) for inst in instances]

                    return self.adapter.batch_update_properties(
                        doc_ids=doc_ids, properties=update_data, collection_name=self.collection
                    )

            # Full update with re-vectorization (slow path)
            logger.info(f"Batch full update with re-vectorization for {self.model_class.__name__}")
            instances = self.filter(filters=filters, limit=limit)

            if not instances:
                logger.info("No documents found matching filters")
                return 0

            # Apply updates to instances
            for inst in instances:
                for key, value in update_data.items():
                    setattr(inst, key, value)

            # Bulk save updated instances
            result = self.bulk_save(instances)
            return result.successful

        except Exception as e:
            logger.error(f"Batch update by filter failed: {e}", exc_info=True)
            return 0

    # ========================================
    # Search Operations
    # ========================================

    def search(
        self, query: str, search_type: SearchType = SearchType.HYBRID, k: int = 10, filters: Any | None = None
    ) -> list[T]:
        """
        Search for model instances using natural language query.

        Args:
            query: Search query text
            search_type: Type of search (NEAR_TEXT, BM25, HYBRID)
            k: Number of results to return
            filters: Optional filter conditions

        Returns:
            List of model instances ranked by relevance
        """
        try:
            results = self.adapter.search(
                query=query, search_type=search_type, k=k, filters=filters, collection_name=self.collection
            )

            instances = []
            for doc in results:
                try:
                    data = self.model_class.from_document(doc)
                    instances.append(data)
                except Exception as e:
                    logger.warning(f"Failed to convert document to model: {e}")

            logger.info(f"Search returned {len(instances)} {self.model_class.__name__} instances")
            return instances

        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            return []

    def filter(self, filters: Any, limit: int = 10) -> list[T]:
        """
        Filter model instances by metadata conditions (no semantic search).

        Args:
            filters: Filter conditions (dict or native format)
            limit: Maximum results to return

        Returns:
            List of matching model instances
        """
        try:
            results = self.adapter.filter_by_metadata(filters=filters, limit=limit, collection_name=self.collection)

            instances = []
            for doc in results:
                try:
                    data = self.model_class.from_document(doc)
                    instances.append(data)
                except Exception as e:
                    logger.warning(f"Failed to convert document: {e}")

            logger.debug(f"Filter returned {len(instances)} {self.model_class.__name__} instances")
            return instances

        except Exception as e:
            logger.error(f"Filter failed: {e}")
            return []

    def search_with_rerank(
        self,
        query: str,
        k: int = 10,
        candidate_k: int | None = None,
        search_type: SearchType = SearchType.HYBRID,
        filters: Any | None = None,
        reranker_type: RerankerProvider = RerankerProvider.FLASHRANK,
        reranker_kwargs: dict[str, Any] | None = None,
    ) -> list[T]:
        """
        Search with reranking for improved relevance.

        Fetches candidate_k results, reranks them, returns top k.

        Args:
            query: Search query
            k: Final number of results
            candidate_k: Number of candidates for reranking (default: k*3)
            search_type: Type of search
            filters: Filter conditions
            reranker_type: Reranker to use
            reranker_kwargs: Additional reranker parameters

        Returns:
            List of reranked model instances
        """
        try:
            if candidate_k is None:
                candidate_k = min(k * 3, 100)

            results = self.adapter.search_with_rerank(
                query=query,
                k=k,
                candidate_k=candidate_k,
                search_type=search_type,
                filters=filters,
                reranker_type=reranker_type,
                reranker_kwargs=reranker_kwargs or {},
                collection_name=self.collection,
            )

            instances = []
            for doc in results:
                try:
                    data = self.model_class.from_document(doc)
                    instances.append(data)
                except Exception as e:
                    logger.warning(f"Failed to convert document: {e}")

            logger.info(f"Rerank search returned {len(instances)} {self.model_class.__name__} instances")
            return instances

        except Exception as e:
            logger.error(f"Rerank search failed: {e}", exc_info=True)
            return []

    # ========================================
    # Retriever Methods (for LangChain integration)
    # ========================================

    def get_retriever(self, search_type: SearchType = SearchType.HYBRID, k: int = 10):
        """
        Get a LangChain retriever for RAG applications.

        Args:
            search_type: Type of search
            k: Number of results

        Returns:
            AdapterRetriever instance
        """
        return AdapterRetriever(
            adapter=self.adapter, collection_name=self.collection, search_type=search_type, search_kwargs={"k": k}
        )

    def get_compression_retriever(
        self,
        reranker_type: RerankerProvider,
        search_type: SearchType = SearchType.HYBRID,
        search_kwargs: dict | None = None,
        reranker_kwargs: dict | None = None,
    ) -> ContextualCompressionRetriever:
        """
        Get a compression retriever with reranking support.

        Args:
            reranker_type: Reranker provider
            search_type: Type of search
            search_kwargs: Search parameters
            reranker_kwargs: Reranker parameters

        Returns:
            ContextualCompressionRetriever with reranking
        """
        from .retrievers.reranker import create_reranker

        base_retriever = self.get_retriever(
            search_type=search_type, k=search_kwargs.get("k", 10) if search_kwargs else 10
        )

        reranker = create_reranker(reranker_type=reranker_type, **(reranker_kwargs or {}))

        return ContextualCompressionRetriever(base_compressor=reranker, base_retriever=base_retriever)

    # ========================================
    # Auto-generated Async Methods
    # ========================================
    asave = async_wrapper(save)
    aget = async_wrapper(get)
    adelete = async_wrapper(delete)
    abulk_save = async_wrapper(bulk_save)
    adelete_by_filter = async_wrapper(delete_by_filter)
    abatch_update_by_filter = async_wrapper(batch_update_by_filter)
    asearch = async_wrapper(search)
    afilter = async_wrapper(filter)
    asearch_with_rerank = async_wrapper(search_with_rerank)
