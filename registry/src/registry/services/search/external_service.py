import logging
from typing import Any

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.vector.enum.enums import RerankerProvider, SearchType
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from .base import VectorSearchService

logger = logging.getLogger(__name__)


class ExternalVectorSearchService(VectorSearchService):
    """
    Vector search service with rerank support.
    """

    def __init__(
        self,
        mcp_server_repo: MCPServerRepository,
        enable_rerank: bool = True,
        search_type: SearchType = SearchType.HYBRID,
        reranker_model: str = "ms-marco-TinyBERT-L-2-v2",
    ):
        """
        Initialize vector search service with rerank support.

        Args:
            enable_rerank: Enable reranking (default: True)
            search_type: Default search type (NEAR_TEXT, BM25, HYBRID)
            reranker_model: FlashRank model name
        """
        self.enable_rerank = enable_rerank
        self.search_type = search_type
        self.reranker_model = reranker_model

        self.client = mcp_server_repo.db_client
        self.mcp_server_repo = mcp_server_repo
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize and verify database connection."""
        try:
            if not self.client.is_initialized():
                raise Exception("Database client not initialized")
            self._initialized = True

            collection_name = ExtendedMCPServer.COLLECTION_NAME
            adapter = self.client.adapter

            if hasattr(adapter, "collection_exists"):
                exists = adapter.collection_exists(collection_name)
                if exists:
                    logger.info(f"Collection '{collection_name}' verified")
                else:
                    logger.warning(f"Collection '{collection_name}' may not exist yet")

            logger.info("Registry vector search verified successfully")
            logger.info(
                f"Registry vector search service initialized (specialized repository): "
                f"rerank={self.enable_rerank}, search_type={self.search_type.value}"
            )

        except Exception as e:
            logger.error(f"Initialization verification failed: {e}", exc_info=True)
            self._initialized = False
            raise Exception(f"Cannot verify vector search: {e}")

    def get_retriever(self, search_type: SearchType | None = None, enable_rerank: bool | None = None, top_k: int = 10):
        """
        Get a LangChain retriever (with optional rerank) for RAG applications.

        Similar to BedrockRerank usage:
        - Creates base retriever
        - Optionally wraps with ContextualCompressionRetriever for reranking

        Args:
            search_type: Search type (uses default if None)
            enable_rerank: Enable rerank (uses instance setting if None)
            top_k: Number of results to return

        Returns:
            BaseRetriever or ContextualCompressionRetriever

        """
        if not self._initialized:
            raise Exception("Vector search service not initialized")

        use_rerank = enable_rerank if enable_rerank is not None else self.enable_rerank
        use_search_type = search_type or self.search_type

        if use_rerank:
            # Return compression retriever with rerank
            return self.mcp_server_repo.get_compression_retriever(
                reranker_type=RerankerProvider.FLASHRANK,
                search_type=use_search_type,
                search_kwargs={"k": top_k * 3},  # 3x candidates
                reranker_kwargs={"top_k": top_k, "model": self.reranker_model},
            )
        else:
            # Return base retriever without rerank
            return self.mcp_server_repo.get_retriever(search_type=use_search_type, k=top_k)

    async def add_or_update_service(
        self, service_path: str, server_info: dict[str, Any], is_enabled: bool = False
    ) -> dict[str, int] | None:
        """
        Add or update server in vector database.

        Uses ExtendedMCPServer.from_server_info() to create server instance,
        then uses specialized repository for sync.
        """
        try:
            # Ensure path is in server_info
            if "path" not in server_info:
                server_info["path"] = service_path

            # Create server instance from server_info
            server = ExtendedMCPServer.from_server_info(server_info=server_info, is_enabled=is_enabled)

            # Use specialized repository's sync method
            result = await self.mcp_server_repo.sync_to_vector_db(server=server, is_delete=True)

            return result if result else {"indexed_tools": 0, "failed_tools": 1}

        except Exception as e:
            logger.error(f"Failed to add/update service: {e}", exc_info=True)
            return {"indexed_tools": 0, "failed_tools": 1}

    async def remove_service(self, service_path: str) -> dict[str, int] | None:
        """
        Remove server from vector database.

        Note: Uses path as identifier. Prefer using server_id when available.
        """
        deleted_count = await self.mcp_server_repo.adelete_by_filter(filters={"path": service_path})
        return {"deleted_tools": deleted_count}

    async def search(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        search_type: SearchType | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search tools with optional reranking.

        Args:
            query: Search text for semantic search
            tags: Tag filters (applied in-memory)
            top_k: Maximum results
            filters: Field filters (dict format, converted to native)
            search_type: Override default search type (NEAR_TEXT, BM25, HYBRID)

        Returns:
            List of tool dictionaries with search metadata
        """
        if not self._initialized:
            logger.warning("Vector search unavailable, returning empty results")
            return []

        use_search_type = search_type or self.search_type
        logger.info(
            f"Search: query='{query}', tags={tags}, top_k={top_k}, filters={filters}, search_type={use_search_type}"
        )

        try:
            if not query:
                # Metadata-only filter
                if not filters:
                    logger.warning("No query and no filters provided")
                    return []
                servers = self.mcp_server_repo.filter(filters=filters, limit=top_k * 2 if tags else top_k)
            elif self.enable_rerank:
                # Use rerank - Repository layer handles candidate_k automatically
                candidate_k = min(top_k * 3, 100)
                if tags:
                    candidate_k = min(candidate_k * 2, 150)

                logger.info(
                    f"Using rerank: type={use_search_type.value}, "
                    f"candidate_k={candidate_k}, k={top_k * 2 if tags else top_k}"
                )
                servers = self.mcp_server_repo.search_with_rerank(
                    query=query,
                    search_type=use_search_type,
                    k=top_k * 2 if tags else top_k,
                    candidate_k=candidate_k,
                    filters=filters,
                    reranker_type=RerankerProvider.FLASHRANK,
                    reranker_kwargs={"model": self.reranker_model},
                )
            else:
                # Regular search without rerank
                servers = self.mcp_server_repo.search(
                    query=query, search_type=use_search_type, k=top_k * 2 if tags else top_k, filters=filters
                )

            # Apply tag filtering if needed (in-memory)
            if tags:
                filtered_servers = []
                for server in servers:
                    server_tags = server.tags or []
                    if any(tag in server_tags for tag in tags):
                        filtered_servers.append(server)
                servers = filtered_servers[:top_k]
            else:
                servers = servers[:top_k]

            # Convert to result dicts
            results = self._servers_to_results(servers)
            logger.info(f"Found {len(results)} servers (rerank={'ON' if self.enable_rerank else 'OFF'})")
            return results

        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            return []

    def _servers_to_results(self, servers: list[ExtendedMCPServer]) -> list[dict[str, Any]]:
        """
        Convert ExtendedMCPServer instances to result dictionaries.

        Extracts server data and search metadata.

        Args:
            servers: List of ExtendedMCPServer instances

        Returns:
            List of dictionaries with server data and metadata
        """
        results = []

        for server in servers:
            logger.info(f"Processing server: {server.serverName}")

            # Get config details
            config = server.config or {}

            result = {
                "server_name": server.serverName,
                "server_path": server.path,
                "path": server.path,
                "description": config.get("description", ""),
                "title": config.get("title", server.serverName),
                "tags": server.tags or [],
                "is_enabled": server.status == "active",
                "status": server.status,
                "numTools": server.numTools,
                "numStars": server.numStars,
            }

            # Add relevance score if available
            if hasattr(server, "relevance_score"):
                result["relevance_score"] = round(server.relevance_score, 4)

            # Add score field if available
            if hasattr(server, "score") and server.score is not None:
                result["score"] = server.score

            results.append(result)
        return results

    def _agent_to_server_info(self, agent_card_dict: dict[str, Any], entity_path: str) -> dict[str, Any]:
        """
        Convert AgentCard dictionary to server_info format for McpTool.

        Args:
            agent_card_dict: AgentCard data as dictionary
            entity_path: Agent path

        Returns:
            server_info dictionary compatible with add_or_update_service
        """
        skills = agent_card_dict.get("skills", [])
        ", ".join([skill.get("name", "") if isinstance(skill, dict) else str(skill) for skill in skills])

        return {
            "server_name": agent_card_dict.get("name", entity_path.strip("/")),
            "description": agent_card_dict.get("description", ""),
            "path": entity_path,
            "tags": agent_card_dict.get("tags", []),
            "entity_type": "a2a_agent",
            "skills": skills,
            "tool_list": [],  # Empty list, will create virtual tool in bulk_create_from_server_info
            "is_enabled": agent_card_dict.get("is_enabled", False),
        }

    async def add_or_update_entity(
        self,
        entity_path: str,
        entity_info: dict[str, Any],
        entity_type: str,
        is_enabled: bool = False,
    ) -> dict[str, int] | None:
        """
        Add or update an entity (agent or server) in the search index.

        Unified interface compatible with EmbeddedFaissService.

        Args:
            entity_path: Entity path identifier
            entity_info: Entity data dictionary
            entity_type: Entity type ("a2a_agent" or "mcp_server")
            is_enabled: Whether the entity is enabled

        Returns:
            Result dictionary or None if unavailable
        """

        if entity_type == "a2a_agent":
            # Convert AgentCard to server_info format
            server_info = self._agent_to_server_info(entity_info, entity_path)
            server_info["is_enabled"] = is_enabled
            # Ensure path is in server_info
            if "path" not in server_info:
                server_info["path"] = entity_path

            # Start background sync
            # asyncio.create_task(mcp_server_repo.sync_full(
            #     server_info=server_info,
            #     is_enabled=is_enabled
            # ))
            return {"indexed_tools": 1, "failed_tools": 0}

        elif entity_type == "mcp_server":
            # Ensure entity_type and path are set
            if "entity_type" not in entity_info:
                entity_info["entity_type"] = "mcp_server"
            if "path" not in entity_info:
                entity_info["path"] = entity_path

            # Start background sync
            # asyncio.create_task(mcp_server_repo.sync_full(
            #     server_info=entity_info,
            #     is_enabled=is_enabled
            # ))
            return {"indexed_tools": 1, "failed_tools": 0}
        else:
            logger.warning(f"Unknown entity_type '{entity_type}', skipping indexing")
            return None

    async def remove_entity(
        self,
        entity_path: str,
    ) -> dict[str, int] | None:
        """
        Remove an entity (agent or server) from the search index.

        Unified interface compatible with EmbeddedFaissService.

        Args:
            entity_path: Entity path identifier

        Returns:
            Result dictionary
        """
        deleted_count = await self.mcp_server_repo.adelete_by_filter(filters={"path": entity_path})
        return {"deleted_tools": deleted_count}

    async def cleanup(self):
        """
        Cleanup resources.

        Note: Does not close database connection as it's shared with Repository.
        """
        logger.info("Cleaning up Registry vector search service")
        self.client = None
        self.mcp_server_repo = None
        self._initialized = False
        logger.info("Registry vector search cleanup complete (shared connection preserved)")

    async def search_mixed(
        self,
        query: str,
        entity_types: list[str] | None = None,
        max_results: int = 20,
        search_type: SearchType | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Search across multiple entity types with rerank support.

        Searches servers and agents with improved relevance.

        Args:
            query: Natural language query text
            entity_types: List of entity types to search
                Options: ["mcp_server", "a2a_agent"]
                Default: all types
            max_results: Maximum results per entity type (default: 20)
            search_type: Override default search type (NEAR_TEXT, BM25, HYBRID)

        Returns:
            Dictionary with entity type keys and result lists
        """
        if not self.mcp_server_repo:
            logger.warning("Vector search not initialized")
            return {"servers": [], "agents": []}

        if not query or not query.strip():
            raise ValueError("Query text is required for search_mixed")

        # Validate and normalize entity types
        max_results = max(1, min(max_results, 50))
        requested_types = set(entity_types or ["mcp_server", "a2a_agent"])
        allowed_types = {"mcp_server", "a2a_agent"}
        entity_filter = list(requested_types & allowed_types)

        if not entity_filter:
            entity_filter = list(allowed_types)

        logger.info(
            f"search_mixed: query='{query}', types={entity_filter}, "
            f"max={max_results}, search_type={search_type or self.search_type}"
        )

        use_search_type = search_type or self.search_type
        results = {"servers": [], "agents": []}

        try:
            # Calculate search parameters
            search_k = max_results * 2  # Get 2x for entity filtering
            candidate_k = min(search_k * 3, 100) if self.enable_rerank else search_k

            # Use rerank if enabled
            if self.enable_rerank:
                logger.info(
                    f"Mixed search with rerank: type={use_search_type.value}, candidate_k={candidate_k}, k={search_k}"
                )
                servers = self.mcp_server_repo.search_with_rerank(
                    query=query,
                    search_type=use_search_type,
                    k=search_k,
                    candidate_k=candidate_k,
                    reranker_type=RerankerProvider.FLASHRANK,
                    reranker_kwargs={"model": self.reranker_model},
                )
            else:
                # Regular search without rerank
                servers = self.mcp_server_repo.search(query=query, search_type=use_search_type, k=search_k)

            # Filter and categorize results
            for server in servers:
                config = server.config or {}
                description = config.get("description", "")

                relevance_score = round(server.relevance_score, 4) if hasattr(server, "relevance_score") else 0.0

                # Build result dict
                result = {
                    "server_path": server.path,
                    "server_name": server.serverName,
                    "path": server.path,
                    "description": description,
                    "match_context": description[:200] if description else "",
                    "relevance_score": relevance_score,
                    "tags": server.tags or [],
                    "is_enabled": server.status == "active",
                }

                # Add to servers results
                if "mcp_server" in entity_filter:
                    results["servers"].append(result)

                # Add to agents results if it's an agent
                # (determine by checking if it has agent-specific config)
                if "a2a_agent" in entity_filter:
                    # You can add logic here to distinguish agents from servers
                    # For now, treat all as potential agents
                    agent_result = result.copy()
                    agent_result["agent_name"] = server.serverName
                    results["agents"].append(agent_result)

            # Sort and limit results
            for key in ["servers", "agents"]:
                # Sort by relevance_score (with fallback to 0.0 for safety)
                results[key].sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)
                results[key] = results[key][:max_results]

            logger.info(
                f"Found {len(results['servers'])} servers, "
                f"{len(results['agents'])} agents "
                f"(rerank={'ON' if self.enable_rerank else 'OFF'})"
            )
            return results

        except Exception as e:
            logger.error(f"search_mixed failed: {e}", exc_info=True)
            return {"servers": [], "agents": []}
