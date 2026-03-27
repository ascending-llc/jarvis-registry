import logging
from typing import Any

import weaviate.classes.config as wvc
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

from ..adapters.adapter import VectorStoreAdapter
from ..enum.enums import EmbeddingProvider, SearchType
from ..retrievers.reranker import create_reranker

logger = logging.getLogger(__name__)


class WeaviateStore(VectorStoreAdapter):
    """
    Weaviate adapter implementation.

    Uses LangChain's WeaviateVectorStore as the underlying implementation.
    Extends with Weaviate-specific features.
    """

    def __init__(self, embedding, config: dict[str, Any], embedding_config: dict[str, Any] = None):
        """Initialize Weaviate adapter."""
        super().__init__(embedding, config, embedding_config)
        self._client = None

    def _get_client(self):
        """Get or create Weaviate client."""
        if self._client is None:
            import weaviate
            from weaviate.auth import AuthApiKey

            self._client = weaviate.connect_to_local(
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 8080),
                grpc_port=self.config.get("grpc_port", 50051),
                auth_credentials=AuthApiKey(self.config.get("api_key")) if self.config.get("api_key") else None,
            )
        return self._client

    def cosine_relevance_score_fn(self, distance: float) -> float:
        """Normalize the distance to a score on a scale [0, 1]."""
        return 1.0 - distance

    def _create_vector_store(self, collection_name: str) -> VectorStore:
        """
        Create LangChain WeaviateVectorStore for collection.

        Ensures collection exists with proper vectorizer configuration for hybrid search.

        Returns:
            WeaviateVectorStore instance
        """
        from langchain_weaviate import WeaviateVectorStore

        # Ensure collection exists with proper configuration
        self._ensure_collection_with_vectorizer(collection_name)

        return WeaviateVectorStore(
            client=self._get_client(),
            index_name=collection_name,
            text_key="content",
            embedding=self.embedding,
            relevance_score_fn=self.cosine_relevance_score_fn,
        )

    def get_vectorizer_config(self):
        """
            Get Vectorizer
        Note: https://docs.weaviate.io/weaviate/configuration/modules#vectorizer-modules
        """
        embedding_provider = self.config.get("embedding_provider", "aws_bedrock")
        if embedding_provider == EmbeddingProvider.AWS_BEDROCK:
            vectorizer_config = wvc.Configure.Vectorizer.text2vec_aws(
                region=self.embedding_config.get("region", "us-east-1"),
                model=self.embedding_config.get("model", "amazon.titan-embed-text-v2:0"),
            )
        elif embedding_provider == EmbeddingProvider.AZURE_OPENAI:
            # Azure OpenAI vectorizer configuration
            vectorizer_config = wvc.Configure.Vectorizer.text2vec_azure_openai(
                resource_name=self.embedding_config.get("resource_name", ""),
                deployment_id=self.embedding_config.get("deployment_name", ""),
            )
        else:
            vectorizer_config = wvc.Configure.Vectorizer.none()
        return vectorizer_config

    def _ensure_collection_with_vectorizer(self, collection_name: str):
        """
        Ensure collection exists with vectorizer configuration for hybrid search.
        """
        client = self._get_client()

        if client.collections.exists(collection_name):
            logger.debug(f"Collection {collection_name} already exists")
            return

        # Create collection with vectorizer configuration
        try:
            logger.info(f"Creating collection {collection_name} with vectorizer configuration...")
            client.collections.create(
                name=collection_name,
                vectorizer_config=self.get_vectorizer_config(),
                properties=[
                    wvc.Property(name="content", data_type=wvc.DataType.TEXT, description="Main searchable content"),
                ],
            )
            logger.info(f"Collection {collection_name} created successfully")
        except Exception as e:
            # If creation fails, collection might already exist (race condition)
            if client.collections.exists(collection_name):
                logger.warning(f"Collection {collection_name} was created by another process")
            else:
                logger.error(f"Failed to create collection {collection_name}: {e}")
                raise

    def close(self):
        """Close Weaviate connection."""
        if self._client:
            self._client.close()
            self._client = None
            self._stores.clear()

    # ========================================
    # Filter normalization (Smart conversion)
    # ========================================

    def _is_native_filter(self, filters: Any) -> bool:
        """Check if filters is Weaviate Filter object."""
        try:
            return hasattr(filters, "__class__") and "Filter" in filters.__class__.__name__
        except Exception as e:
            logger.error(f"_is_native_filter: {e}")
            return False

    def _dict_to_native_filter(self, filters: dict[str, Any]):
        """
        Convert dict to Weaviate Filter object.

        Supports:
        - Simple: {"key": "value"}
        - Operators: {"key": {"$gt": 100}}
        - Combining: {"$and": [...], "$or": [...]}
        """
        from weaviate.classes.query import Filter

        def convert_condition(key: str, value: Any):
            # Handle explicit operator dict: {"$in": [...], "$gt": 100}
            if isinstance(value, dict):
                filter_obj = None
                for op, val in value.items():
                    if op == "$eq":
                        f = Filter.by_property(key).equal(val)
                    elif op == "$ne":
                        f = Filter.by_property(key).not_equal(val)
                    elif op == "$gt":
                        f = Filter.by_property(key).greater_than(val)
                    elif op == "$gte":
                        f = Filter.by_property(key).greater_or_equal(val)
                    elif op == "$lt":
                        f = Filter.by_property(key).less_than(val)
                    elif op == "$lte":
                        f = Filter.by_property(key).less_or_equal(val)
                    elif op == "$in":
                        f = Filter.by_property(key).contains_any(val)
                    else:
                        logger.warning(f"Unsupported operator: {op}")
                        continue
                    filter_obj = f if filter_obj is None else (filter_obj & f)
                return filter_obj
            # Handle list values: automatically convert to $in operator
            elif isinstance(value, list):
                if len(value) == 0:
                    logger.warning(f"Empty list for key '{key}', skipping filter")
                    raise ValueError("Empty list for key '{key}', skipping filter")
                elif len(value) == 1:
                    return Filter.by_property(key).equal(value[0])
                else:
                    return Filter.by_property(key).contains_any(value)
            # Handle simple equality: {"key": "value"}
            else:
                return Filter.by_property(key).equal(value)

        def parse_filters(f: dict[str, Any]):
            if "$and" in f:
                conditions = [parse_filters(c) if isinstance(c, dict) else c for c in f["$and"]]
                result = conditions[0]
                for c in conditions[1:]:
                    result = result & c
                return result
            elif "$or" in f:
                conditions = [parse_filters(c) if isinstance(c, dict) else c for c in f["$or"]]
                result = conditions[0]
                for c in conditions[1:]:
                    result = result | c
                return result
            else:
                filter_objs = []
                for key, value in f.items():
                    if key not in ["$and", "$or", "$not"]:
                        filter_objs.append(convert_condition(key, value))

                if not filter_objs:
                    return None
                result = filter_objs[0]
                for f in filter_objs[1:]:
                    result = result & f
                return result

        return parse_filters(filters)

    def normalize_filters(self, filters: Any):
        """Convert dict filters to Weaviate Filter or return native object."""
        if filters is None:
            return None
        if self._is_native_filter(filters):
            return filters
        if isinstance(filters, dict):
            try:
                return self._dict_to_native_filter(filters)
            except Exception as error:
                logger.error(f"Failed to convert dict filters: {error}")
                return None
        logger.warning(f"Unsupported filter type: {type(filters)}")
        return None

    # ========================================
    # Extended features implementation
    # ========================================

    def get_collection(self, collection_name: str | None = None):
        """
        Get Weaviate collection by name.

        Args:
            collection_name: Collection name (required, will use adapter default if None)

        Returns:
            Weaviate collection object

        Raises:
            ValueError: If collection_name is None and no default is set
            Exception: If collection does not exist
        """
        if collection_name is None:
            collection_name = self._default_collection
            if not collection_name:
                raise ValueError(
                    "collection_name is required but was not provided. "
                    "This usually means the adapter was not properly initialized with a collection name."
                )

        client = self._get_client()
        if self.collection_exists(collection_name):
            return client.collections.get(collection_name)
        raise Exception(f"Failed to get collection: {collection_name}")

    def get_by_id(self, doc_id: str, collection_name: str | None = None) -> Document | None:
        """
        Get document by ID using Weaviate client.

        Args:
            doc_id: Document ID (UUID)
            collection_name: Collection name

        Returns:
            LangChain Document or None
        """
        collection = self.get_collection(collection_name)
        try:
            obj = collection.query.fetch_object_by_id(doc_id)
            if obj:
                return Document(
                    page_content=obj.properties.get("content", ""), metadata=obj.properties, id=str(obj.uuid)
                )
        except Exception as e:
            logger.error(f"Failed to get document by ID: {e}")

        return None

    def get_by_ids(self, ids: list[str], collection_name: str | None = None) -> list[Document]:
        """
        Get multiple documents by IDs using Weaviate client.

        Args:
            ids: List of document IDs (UUIDs)
            collection_name: Collection name

        Returns:
            List of LangChain Documents (may be less than requested if some IDs not found)
        """
        if not ids:
            return []

        documents = []
        for doc_id in ids:
            doc = self.get_by_id(doc_id, collection_name)
            if doc:
                documents.append(doc)

        return documents

    def list_collections(self) -> list[str]:
        """List all Weaviate collections."""
        try:
            client = self._get_client()
            collections = client.collections.list_all()
            if isinstance(collections, dict):
                return list(collections.keys())
        except Exception as e:
            logger.error(f"Failed to list collections: {e}")
            return list(self._stores.keys())

    def collection_exists(self, collection_name: str | None = None) -> bool:
        """
        Check if collection exists in Weaviate.

        Args:
            collection_name: Collection name (required, will use adapter default if None)

        Returns:
            True if collection exists

        Raises:
            ValueError: If collection_name is None and no default is set
        """
        if collection_name is None:
            collection_name = self._default_collection
            if not collection_name:
                raise ValueError(
                    "collection_name is required but was not provided. "
                    "This usually means the adapter was not properly initialized with a collection name."
                )

        try:
            client = self._get_client()
            return client.collections.exists(collection_name)
        except Exception as e:
            logger.error(f"Failed to check collection existence: {e}")
            return collection_name in self._stores

    def has_property(self, collection_name: str, property_name: str) -> bool:
        """
        Check whether a property exists in Weaviate collection schema.
        """
        if not self.collection_exists(collection_name):
            return False
        try:
            collection = self.get_collection(collection_name)
            config = collection.config.get()
            properties = getattr(config, "properties", None)
            if not properties:
                return False

            for prop in properties:
                if isinstance(prop, dict):
                    if prop.get("name") == property_name:
                        return True
                    continue
                if getattr(prop, "name", None) == property_name:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Failed to inspect collection property '{property_name}': {e}")
            return False

    def filter_by_metadata(
        self, filters: Any, limit: int = 100, collection_name: str | None = None, **kwargs
    ) -> list[Document]:
        """Implement Weaviate metadata filtering (filters already normalized)."""
        collection = self.get_collection(collection_name)
        normalized_filters = self.normalize_filters(filters)
        try:
            response = collection.query.fetch_objects(filters=normalized_filters, limit=limit)
            docs = self.get_document_response(response)
            return docs
        except Exception as e:
            logger.error(f"Filter by metadata failed: {e}")
            return []

    def bm25_search(
        self, query: str, k: int = 10, filters: Any = None, collection_name: str | None = None, **kwargs
    ) -> list[Document]:
        collection = self.get_collection(collection_name)
        normalized_filters = self.normalize_filters(filters)
        try:
            response = collection.query.bm25(query=query, limit=k, filters=normalized_filters, **kwargs)
            docs = self.get_document_response(response)
            return docs
        except Exception as e:
            logger.error(f"bm25 text failed: {e}")
            return []

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
        Hybrid search combining BM25 and vector search.

        For external embeddings, we need to provide the query vector manually.
        """
        collection = self.get_collection(collection_name)
        normalized_filters = self.normalize_filters(filters)
        try:
            # Generate query vector using external embedding
            query_vector = self.embedding.embed_query(query)

            # Perform hybrid search with explicit vector
            response = collection.query.hybrid(
                query=query,
                vector=query_vector,  # Provide external embedding vector
                alpha=alpha,  # 0=BM25 only, 1=vector only, 0.5=balanced
                limit=k,
                filters=normalized_filters,
                **kwargs,
            )
            docs = self.get_document_response(response)
            return docs
        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            logger.info("Falling back to vector search with external embeddings...")
            # Fallback to near_vector search if hybrid fails
            # This uses external embeddings and doesn't require Weaviate's built-in vectorizer
            try:
                return self.near_vector(query=query, k=k, filters=filters, collection_name=collection_name)
            except Exception as fallback_error:
                logger.error(f"Fallback vector search also failed: {fallback_error}")
                return []

    def near_vector(
        self, query: str, k: int = 10, filters: Any = None, collection_name: str | None = None, **kwargs
    ) -> list[Document]:
        """
        Vector similarity search using external embeddings.
        """
        collection = self.get_collection(collection_name)
        normalized_filters = self.normalize_filters(filters)
        try:
            # Generate query vector using external embedding
            query_vector = self.embedding.embed_query(query)

            # Perform vector similarity search
            response = collection.query.near_vector(
                near_vector=query_vector, limit=k, filters=normalized_filters, **kwargs
            )
            docs = self.get_document_response(response)
            return docs
        except Exception as e:
            logger.error(f"Near vector search failed: {e}")
            return []

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
        Note:  A vectorizer needs to be configured before this function can be used.
        """
        collection = self.get_collection(collection_name)
        normalized_filters = self.normalize_filters(filters)
        try:
            response = collection.query.near_text(query=query, limit=k, filters=normalized_filters, **kwargs)
            docs = self.get_document_response(response)
            return docs
        except Exception as e:
            logger.error(f"Near text failed: {e}")
            return []

    def search(
        self,
        query: str,
        search_type: SearchType = SearchType.NEAR_TEXT,
        k: int = 10,
        filters: Any = None,
        collection_name: str | None = None,
        **kwargs,
    ) -> list[Document]:
        if search_type == SearchType.BM25:
            return self.bm25_search(query=query, k=k, filters=filters, collection_name=collection_name, **kwargs)
        elif search_type == SearchType.HYBRID:
            return self.hybrid_search(query=query, k=k, filters=filters, collection_name=collection_name, **kwargs)
        elif search_type == SearchType.NEAR_TEXT:
            return self.near_text(query=query, k=k, filters=filters, collection_name=collection_name, **kwargs)
        elif search_type == SearchType.NEAR_VECTOR:
            return self.near_vector(query=query, k=k, filters=filters, collection_name=collection_name, **kwargs)
        else:
            logger.error(f"Unknown search type: {search_type}")
            raise ValueError(f"Unknown search type: {search_type}")

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
            collection_name: Target collection

        Returns:
            List of reranked Documents
        """
        try:
            filters = self.normalize_filters(filters)
            # Default candidate_k to 3x final k
            if candidate_k is None:
                candidate_k = min(k * 3, 100)

            # Step 1: Get candidate documents
            logger.debug(f"Fetching {candidate_k} candidates for reranking")
            candidates = self.search(
                query=query,
                search_type=search_type,
                k=candidate_k,
                filters=filters,
                collection_name=collection_name,
                **kwargs,
            )

            if not candidates:
                logger.warning("No candidates found for reranking")
                return []

            # Step 2: Rerank candidates
            logger.debug(f"Reranking {len(candidates)} candidates to get top {k}")
            reranker_kwargs = reranker_kwargs or {}
            reranker_kwargs["top_n"] = k

            reranker = create_reranker(reranker_type=reranker_type, **reranker_kwargs)

            # Rerank and return top k
            reranked = reranker.compress_documents(documents=candidates, query=query)

            logger.info(f"Reranking complete: {len(candidates)} -> {len(reranked)} results")
            return reranked

        except Exception as e:
            logger.error(f"Reranking failed: {e}", exc_info=True)
            logger.warning("Falling back to regular search without reranking")
            # Fallback to regular search
            return self.search(
                query=query, search_type=search_type, k=k, filters=filters, collection_name=collection_name, **kwargs
            )

    def batch_update_properties(self, doc_ids: list[str], update_data: dict[str, Any], collection_name: str) -> int:
        """
        Batch update properties using native Weaviate batch update.

        Args:
            doc_ids: List of document UUIDs to update
            update_data: Dictionary of properties to update (metadata only)
            collection_name: Collection name

        Returns:
            Number of successfully updated documents
        """
        if not doc_ids:
            return 0

        collection = self.get_collection(collection_name)

        try:
            # Filter out vector fields to avoid re-vectorization
            safe_update_data = {k: v for k, v in update_data.items() if k != "content"}

            if not safe_update_data:
                logger.warning("No safe fields to update (content field excluded)")
                return 0

            updated_count = 0
            failed_count = 0
            chunk_size = 100

            # Process in chunks using batch context
            for i in range(0, len(doc_ids), chunk_size):
                chunk_ids = doc_ids[i : i + chunk_size]

                with collection.batch.dynamic() as batch:
                    for doc_id in chunk_ids:
                        try:
                            collection.data.update(uuid=doc_id, properties=safe_update_data)
                            updated_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to update {doc_id}: {e}")
                            failed_count += 1

                # Check for batch errors
                if hasattr(batch, "failed_objects") and batch.failed_objects:
                    failed_count += len(batch.failed_objects)

            logger.info(f"Batch updated {updated_count}/{len(doc_ids)} documents (failed: {failed_count})")
            return updated_count

        except Exception as e:
            logger.error(f"Batch update failed: {e}", exc_info=True)
            return 0

    def delete_by_filter(self, filters: Any, collection_name: str | None = None) -> int:
        """
        Delete documents by filter conditions.

        Supports both dict and native Weaviate Filter formats:
        - Dict: {"server_id": "xxx"} or {"status": {"$in": ["active", "inactive"]}}
        - Native: weaviate.classes.query.Filter object

        Args:
            filters: Filter conditions (auto-converted if dict)
            collection_name: Collection name

        Returns:
            Number of successfully deleted documents
        """
        collection = self.get_collection(collection_name)

        try:
            normalized_filters = self.normalize_filters(filters)
            if normalized_filters is None:
                logger.warning("No valid filters provided for deletion")
                return 0
            # Execute batch delete
            result = collection.data.delete_many(where=normalized_filters)
            deleted_count = result.successful

            if result.failed > 0:
                logger.warning(f"Batch delete: {result.successful} successful, {result.failed} failed")

            logger.info(f"Deleted {deleted_count} documents by filter")
            return deleted_count

        except Exception as e:
            logger.error(f"Delete by filter failed: {e}", exc_info=True)
            return 0

    @staticmethod
    def get_document_response(response):
        docs = []
        for obj in response.objects:
            doc = Document(page_content=obj.properties.get("content", ""), metadata=obj.properties, id=str(obj.uuid))
            docs.append(doc)
        return docs

    def update_metadata(self, doc_id: str, metadata: dict[str, Any], collection_name: str | None = None) -> bool:
        """
        Update metadata properties without re-vectorization (Weaviate-specific).

        Only updates the metadata properties, leaves vector unchanged.
        This is much faster than delete+re-insert.

        Args:
            doc_id: Document UUID
            metadata: Metadata properties to update
            collection_name: Collection name

        Returns:
            True if updated successfully
        """
        collection = self.get_collection(collection_name)

        try:
            # Filter out non-property fields
            properties = {k: v for k, v in metadata.items() if k != "collection"}

            # Update properties using Weaviate's update API
            collection.data.update(uuid=doc_id, properties=properties)

            logger.info(f"Updated metadata for document {doc_id}: {list(properties.keys())}")
            return True

        except Exception as e:
            logger.error(f"Metadata update failed: {e}", exc_info=True)
            return False
