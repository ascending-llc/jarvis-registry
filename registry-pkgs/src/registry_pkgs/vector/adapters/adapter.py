import logging
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

from ..enum.enums import SearchType

logger = logging.getLogger(__name__)


class VectorStoreAdapter(ABC):
    """
    Vector Store Adapter

    Design Pattern: Adapter Pattern

    Responsibilities:
    1. Adapt different VectorStore implementations (Weaviate)
    2. Proxy standard VectorStore operations
    3. Extend with database-specific features (get_by_id, list_collections, etc.)
    4. Manage collections and connections
    5. Provide unified interface for Repository
    """

    def __init__(self, embedding, config: dict[str, Any], embedding_config: dict[str, Any] = None):
        """
        Initialize adapter with embedding and config.

        Args:
            embedding: LangChain embedding instance
            config: Database configuration
            embedding_config: Optional embedding-specific config
        """
        self.embedding = embedding
        self.config = config
        self.embedding_config = embedding_config or {}

        # Collection management
        self._default_collection = config.get("collection_name", "Default")
        self._stores: dict[str, VectorStore] = {}  # collection_name -> LangChain VectorStore

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager, close connections."""
        self.close()

    # ========================================
    # Abstract methods - Subclasses must implement
    # ========================================

    @abstractmethod
    def _create_vector_store(self, collection_name: str) -> VectorStore:
        """
        Create LangChain VectorStore instance for collection.

        Args:
            collection_name: Name of the collection

        Returns:
            LangChain VectorStore instance (WeaviateVectorStore, etc.)
        """
        pass

    @abstractmethod
    def close(self):
        """Close all connections and clean up resources."""
        pass

    # ========================================
    # Collection management
    # ========================================

    def get_vector_store(self, collection_name: str | None = None) -> VectorStore:
        """
        Get or create LangChain VectorStore for collection.

        Args:
            collection_name: Collection name (uses default if None)

        Returns:
            LangChain VectorStore instance
        """
        name = collection_name or self._default_collection

        if name not in self._stores:
            self._stores[name] = self._create_vector_store(name)

        return self._stores[name]

    # ========================================
    # Standard VectorStore operations (Proxy)
    # These directly use LangChain VectorStore methods
    # ========================================

    def similarity_search(
        self, query: str, k: int = 10, filters: Any = None, collection_name: str | None = None, **kwargs
    ) -> list[Document]:
        """
        Proxy to VectorStore.similarity_search() with smart filter handling.

        Automatically converts dict filters to native format.

        Args:
            query: Search query text
            k: Number of results
            filters: Filter object (auto-converted if dict)
                - Native format (Filter object) → used directly
                - Dict format → converted to native
                - None → no filtering
            collection_name: Target collection
            **kwargs: Additional parameters

        Returns:
            List of LangChain Document objects

        """
        store = self.get_vector_store(collection_name)

        # Smart filter normalization
        native_filters = self.normalize_filters(filters)

        return store.similarity_search(query, k=k, filters=native_filters, **kwargs)

    def normalize_filters(self, filters: Any) -> Any:
        """
        Normalize filters to native format.

        Handles both native and dict formats automatically.

        Args:
            filters: Any filter format

        Returns:
            Native filter format for the database
        """
        if filters is None:
            return None

        # If already native format, use directly
        if self._is_native_filter(filters):
            return filters

        # If dict, convert to native format
        if isinstance(filters, dict):
            return self._dict_to_native_filter(filters)

        # Unknown format, pass through (let database handle/error)
        logger.warning(f"Unknown filter format: {type(filters)}, passing through")
        return filters

    @abstractmethod
    def _is_native_filter(self, filters: Any) -> bool:
        """
        Check if filters is already in native format.

        Args:
            filters: Filter object to check

        Returns:
            True if native format
        """
        pass

    @abstractmethod
    def _dict_to_native_filter(self, filters: dict[str, Any]) -> Any:
        """
        Convert dict to native filter format.

        Handles basic conversions:
        - Simple equality: {"key": "value"}
        - Operators: {"key": {"$gt": 100}}
        - Combining with $and, $or

        Args:
            filters: Dict filters

        Returns:
            Native filter object
        """
        pass

    def add_documents(self, documents: list[Document], collection_name: str | None = None, **kwargs) -> list[str]:
        """
        Proxy to VectorStore.add_documents()

        Args:
            documents: List of LangChain Document objects
            collection_name: Target collection
            **kwargs: Additional parameters

        Returns:
            List of assigned document IDs
        """
        store = self.get_vector_store(collection_name)
        return store.add_documents(documents, **kwargs)

    def delete(self, ids: list[str], collection_name: str | None = None, **kwargs) -> bool | None:
        """
        Proxy to VectorStore.delete()

        Args:
            ids: List of document IDs to delete
            collection_name: Target collection
            **kwargs: Additional parameters

        Returns:
            True if successful, False/None otherwise
        """
        store = self.get_vector_store(collection_name)
        try:
            return store.delete(ids, **kwargs)
        except Exception as e:
            logger.error(f"Failed to delete documents: {e}")
            return False

    # ========================================
    # Extended functionality - Not in base VectorStore
    # Subclasses should implement these
    # ========================================

    def get_by_id(self, doc_id: str, collection_name: str | None = None) -> Document | None:
        """
        Extended feature: Get document by ID

        VectorStore doesn't provide this functionality.
        Subclasses must implement using database-specific APIs.

        Args:
            doc_id: Document ID
            collection_name: Target collection

        Returns:
            LangChain Document or None if not found
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_by_id(). "
            "Use database-specific API to retrieve document by ID."
        )

    def get_by_ids(self, ids: list[str], collection_name: str | None = None) -> list[Document]:
        """
        Extended feature: Get multiple documents by IDs

        Args:
            ids: List of document IDs
            collection_name: Target collection

        Returns:
            List of LangChain Documents (may be less than requested if some IDs not found)
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_by_ids(). "
            "Use database-specific API to retrieve documents by IDs."
        )

    @abstractmethod
    def filter_by_metadata(
        self, filters: Any, limit: int, collection_name: str | None = None, **kwargs
    ) -> list[Document]:
        """
        Extended feature: Filter documents by metadata only (no vector search)

        Automatically converts dict filters to native format.

        Args:
            filters: Filter object (auto-converted if dict)
            limit: Maximum number of results
            collection_name: Target collection

        Returns:
            List of matching LangChain Documents

        """
        raise NotImplementedError()

    @abstractmethod
    def bm25_search(
        self, query: str, k: int = 10, filters: Any = None, collection_name: str | None = None, **kwargs
    ) -> list[Document]:
        """
        Extended feature: BM25 search (Keyword search)
        """
        raise NotImplementedError()

    @abstractmethod
    def hybrid_search(
        self,
        query: str,
        k: int = 10,
        alpha: float = 0.5,
        filters: Any = None,
        collection_name: str | None = None,
        **kwargs,
    ) -> list[Document]:
        """
        Extended feature: Hybrid search
        """
        raise NotImplementedError()

    @abstractmethod
    def near_text(
        self,
        query: str,
        k: int = 10,
        alpha: float = 0.5,
        filters: Any = None,
        collection_name: str | None = None,
        **kwargs,
    ) -> list[Document]:
        """
        Extended feature: Near text
        find objects with the nearest vector to an input text
        """
        raise NotImplementedError()

    @abstractmethod
    def search(
        self,
        query: str,
        search_type: SearchType = SearchType.NEAR_TEXT,
        k: int = 10,
        filters: Any = None,
        collection_name: str | None = None,
        **kwargs,
    ) -> list[Document]:
        """
        Extended feature: Search by search type
        """
        raise NotImplementedError()

    def search_with_rerank(
        self,
        query: str,
        k: int = 10,
        candidate_k: int | None = None,
        search_type: SearchType = SearchType.HYBRID,
        filters: Any = None,
        reranker_type: str = "flashrank",
        reranker_kwargs: dict[str, Any] | None = None,
        collection_name: str | None = None,
        **kwargs,
    ) -> list[Document]:
        """
        Extended feature: Search with reranking for improved relevance.

        Fetches candidate_k results, reranks them, returns top k.

        Args:
            query: Search query
            k: Final number of results
            candidate_k: Number of candidates for reranking (default: k*3)
            search_type: Type of search
            filters: Filter conditions
            reranker_type: Reranker to use
            reranker_kwargs: Additional reranker parameters
            collection_name: Target collection

        Returns:
            List of reranked Documents
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement search_with_rerank(). "
            "Use search() + reranker to implement this functionality."
        )

    def list_collections(self) -> list[str]:
        """
        Extended feature: List all collections

        VectorStore doesn't provide this functionality.
        Subclasses should implement using database-specific APIs.

        Returns:
            List of collection names
        """
        # Default implementation: return initialized collections
        return list(self._stores.keys())

    def collection_exists(self, collection_name: str) -> bool:
        """
        Extended feature: Check if collection exists

        Args:
            collection_name: Collection name to check

        Returns:
            True if collection exists
        """
        return collection_name in self.list_collections()

    def has_property(self, collection_name: str, property_name: str) -> bool:
        """
        Check whether a metadata/property field exists in collection schema.

        Default adapter behavior is permissive (True). Backends with explicit schema
        constraints (e.g., Weaviate) should override this.
        """
        return True

    def update_metadata(self, doc_id: str, metadata: dict[str, Any], collection_name: str | None = None) -> bool:
        """
        Update metadata fields only without re-vectorization.

        This is an optimization for updating non-content fields
        like tags, status, scope, etc.

        Args:
            doc_id: Document ID
            metadata: Metadata fields to update
            collection_name: Target collection

        Returns:
            True if updated successfully
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement update_metadata(). "
            "Falling back to full update with re-vectorization."
        )

    def delete_by_filter(self, filters: Any, collection_name: str | None = None) -> int:
        """
        Extended feature: Delete documents by filter conditions

        Automatically converts dict filters to native format before deletion.

        Args:
            filters: Filter object (auto-converted if dict)
                - Dict: {"field": "value"} or {"field": {"$in": ["val1", "val2"]}}
                - Native format: Database-specific filter object
            collection_name: Target collection

        Returns:
            Number of deleted documents
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement delete_by_filter(). "
            "Use database-specific API to delete documents by filter."
        )

    # ========================================
    # Utility methods
    # ========================================

    def get_default_collection(self) -> str:
        """Get default collection name."""
        return self._default_collection

    def describe(self) -> dict[str, Any]:
        """Get adapter description and status."""
        return {
            "type": type(self).__name__,
            "default_collection": self._default_collection,
            "initialized_collections": list(self._stores.keys()),
            "config": {k: v for k, v in self.config.items() if k not in ["api_key", "password"]},
        }
