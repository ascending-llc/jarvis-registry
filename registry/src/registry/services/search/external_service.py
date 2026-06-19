import logging
from typing import Any

from registry_pkgs.models.enums import MCPEntityType
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.vector.enum.enums import RerankerProvider, SearchType
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from .base import VectorSearchService

logger = logging.getLogger(__name__)


class ExternalVectorSearchService(VectorSearchService):
    """
    Vector search service with rerank support.
    """

    _ALL_MCP_VECTOR_TYPES = [MCPEntityType.TOOL, MCPEntityType.RESOURCE, MCPEntityType.PROMPT]

    def __init__(
        self,
        mcp_server_repo: MCPServerRepository,
        enable_rerank: bool = True,
        search_type: SearchType = SearchType.HYBRID,
    ):
        """
        Initialize vector search service with rerank support.

        Args:
            enable_rerank: Enable Bedrock reranking (default: True)
            search_type: Default search type (NEAR_TEXT, BM25, HYBRID)
        """
        self.enable_rerank = enable_rerank
        self.search_type = search_type

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

    async def add_or_update_service(
        self, service_path: str, server_info: dict[str, Any], is_enabled: bool = False
    ) -> dict[str, Any] | None:
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
            return result.to_dict()

        except Exception as e:
            logger.error(f"Failed to add/update service: {e}", exc_info=True)
            return {"indexed": 0, "failed": 1, "deleted": 0, "metadata_updated": 0, "version": None, "error": str(e)}

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
                    reranker_type=RerankerProvider.BEDROCK_COHERE,
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
                "is_enabled": config.get("enabled", False),
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

    @staticmethod
    def _tool_doc_to_result(doc: dict[str, Any]) -> dict[str, Any]:
        """Map a reranked tool document (from ExtendedMCPServer.from_document) to a tool result."""
        description = doc.get("description") or ""
        match_context = doc.get("match_context") or description[:200]
        return {
            "entity_type": "tool",
            "server_path": doc.get("path") or "",
            "server_name": doc.get("server_name") or "",
            "tool_name": doc.get("tool_name") or "",
            "description": description,
            "relevance_score": doc.get("relevance_score") or 0.0,
            "match_context": match_context,
        }

    @classmethod
    def _mcp_search_entity_types(cls, want_servers: bool) -> list[MCPEntityType]:
        """Choose vector document types needed for the requested MCP search shape."""
        if want_servers:
            return cls._ALL_MCP_VECTOR_TYPES
        return [MCPEntityType.TOOL]

    async def search_mixed(
        self,
        query: str,
        entity_types: list[str] | None = None,
        max_results: int = 20,
        search_type: SearchType | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Search MCP servers and tools with rerank support.

        Args:
            query: Natural language query text
            entity_types: Subset of ["mcp_server", "tool"]; default both.
            max_results: Maximum results per result list (default: 20)
            search_type: Override default search type (NEAR_TEXT, BM25, HYBRID)

        Returns:
            ``{"servers": [...], "tools": [...]}`` shaped for the /search route mappers.
        """
        if not self.mcp_server_repo:
            logger.warning("Vector search not initialized")
            return {"servers": [], "tools": []}

        if not query or not query.strip():
            raise ValueError("Query text is required for search_mixed")

        max_results = max(1, min(max_results, 50))
        requested_types = set(entity_types or ["mcp_server", "tool"])
        entity_filter = requested_types & {"mcp_server", "tool"} or {"mcp_server", "tool"}
        want_servers = "mcp_server" in entity_filter
        want_tools = "tool" in entity_filter

        use_search_type = search_type or self.search_type
        logger.info(
            f"search_mixed: query='{query}', types={sorted(entity_filter)}, "
            f"max={max_results}, search_type={use_search_type.value}"
        )

        search_k = max_results * 2  # extra headroom so grouping still yields enough servers
        filters = {"entity_type": self._mcp_search_entity_types(want_servers)}

        try:
            if self.enable_rerank:
                candidate_k = min(search_k * 3, 100)
                docs = await self.mcp_server_repo.asearch_with_rerank(
                    query=query,
                    k=search_k,
                    candidate_k=candidate_k,
                    search_type=use_search_type,
                    filters=filters,
                    reranker_type=RerankerProvider.BEDROCK_COHERE,
                )
            else:
                docs = await self.mcp_server_repo.asearch(
                    query=query,
                    search_type=use_search_type,
                    k=search_k,
                    filters=filters,
                )

            tools: list[dict[str, Any]] = []
            servers_by_key: dict[str, dict[str, Any]] = {}

            for doc in docs:
                tool_result = None
                if doc.get("entity_type") == MCPEntityType.TOOL:
                    tool_result = self._tool_doc_to_result(doc)
                if want_tools and tool_result is not None:
                    tools.append(tool_result)

                if want_servers:
                    self._accumulate_server(servers_by_key, doc, tool_result)

            servers = sorted(servers_by_key.values(), key=lambda s: s["relevance_score"], reverse=True)[:max_results]
            self._limit_matching_tools(servers, max_results)
            tools.sort(key=lambda t: t["relevance_score"], reverse=True)
            tools = tools[:max_results]

            logger.info(
                f"Found {len(servers)} servers, {len(tools)} tools (rerank={'ON' if self.enable_rerank else 'OFF'})"
            )
            return {"servers": servers, "tools": tools}

        except RuntimeError as e:
            logger.error(f"search_mixed failed: {e}", exc_info=True)
            return {"servers": [], "tools": []}

    @staticmethod
    def _limit_matching_tools(servers: list[dict[str, Any]], max_results: int) -> None:
        """Keep matched tools bounded and sorted within each server result."""
        for server in servers:
            matching_tools = server.get("matching_tools", [])
            matching_tools.sort(key=lambda tool: tool.get("relevance_score", 0.0), reverse=True)
            server["matching_tools"] = matching_tools[:max_results]

    @staticmethod
    def _accumulate_server(
        servers_by_key: dict[str, dict[str, Any]],
        doc: dict[str, Any],
        tool_result: dict[str, Any] | None,
    ) -> None:
        """Group a matched MCP document under its owning server, building a server result in place."""
        key = doc.get("server_id") or doc.get("path")
        if not key:
            logger.warning("Skipping MCP search doc without server_id/path: %s", doc.get("server_name"))
            return

        score = doc.get("relevance_score") or 0.0
        description = doc.get("description") or ""
        match_context = doc.get("match_context") or description[:200]
        server = servers_by_key.get(key)
        if server is None:
            server = {
                "entity_type": "mcp_server",
                "server_id": doc.get("server_id"),
                "path": doc.get("path") or "",
                "server_name": doc.get("server_name") or "",
                "description": description,
                "tags": doc.get("tags") or [],
                "is_enabled": doc.get("is_enabled", False),
                "num_tools": 0,
                "relevance_score": score,
                "match_context": match_context,
                "matching_tools": [],
            }
            servers_by_key[key] = server
        else:
            if score > server["relevance_score"]:
                server["relevance_score"] = score
                server["description"] = description
                server["match_context"] = match_context

        if tool_result is not None:
            server["matching_tools"].append(
                {
                    "tool_name": tool_result["tool_name"],
                    "description": tool_result["description"],
                    "relevance_score": score,
                    "match_context": tool_result["match_context"],
                }
            )
