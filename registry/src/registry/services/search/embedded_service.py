"""
Embedded FAISS-based vector search service.

This implementation uses FAISS with sentence-transformers for local vector search.
Requires heavy dependencies: torch, sentence-transformers, faiss-cpu.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

from pydantic import HttpUrl

from ...core.config import Settings
from .base import VectorSearchService

logger = logging.getLogger(__name__)

# Try to import heavy dependencies - they may not be installed if using external mode
try:
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    FAISS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"FAISS dependencies not available: {e}. Use discovery_mode=external or install dependencies.")
    FAISS_AVAILABLE = False
    faiss = None
    np = None
    SentenceTransformer = None


class _PydanticAwareJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles Pydantic and standard types."""

    def default(
        self,
        o: Any,
    ) -> Any:
        """Convert non-serializable types to JSON-compatible formats."""
        if isinstance(o, HttpUrl):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


class EmbeddedFaissService(VectorSearchService):
    """Embedded vector search using FAISS and sentence-transformers."""

    def __init__(self, settings: Settings):
        """
        Initialize the embedded FAISS service.

        Args:
            settings: Application settings object
        """
        if not FAISS_AVAILABLE:
            raise ImportError(
                "FAISS dependencies not installed. "
                "Install with: pip install faiss-cpu sentence-transformers torch OR "
                "use discovery_mode=external in your configuration."
            )

        self.settings = settings
        self.embedding_model: SentenceTransformer | None = None
        self.faiss_index: faiss.IndexIDMap | None = None
        self.metadata_store: dict[str, dict[str, Any]] = {}
        self.next_id_counter: int = 0

    async def initialize(self):
        """Initialize the FAISS service - load model and index."""
        await self._load_embedding_model()
        await self._load_faiss_data()

    async def _load_embedding_model(self):
        """Load the sentence transformer model."""
        logger.info("Loading FAISS data and embedding model...")

        # Ensure servers directory exists
        self.settings.servers_dir.mkdir(parents=True, exist_ok=True)

        try:
            model_cache_path = self.settings.container_registry_dir / ".cache"
            model_cache_path.mkdir(parents=True, exist_ok=True)

            # Set cache path for sentence transformers
            import os

            original_st_home = os.environ.get("SENTENCE_TRANSFORMERS_HOME")
            os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(model_cache_path)

            # Check if local model exists
            model_path = self.settings.embeddings_model_dir
            model_exists = model_path.exists() and any(model_path.iterdir()) if model_path.exists() else False

            if model_exists:
                logger.info(f"Loading SentenceTransformer model from local path: {self.settings.embeddings_model_dir}")
                self.embedding_model = SentenceTransformer(str(self.settings.embeddings_model_dir))
            else:
                logger.info(
                    f"Local model not found at {self.settings.embeddings_model_dir}, downloading from Hugging Face"
                )
                self.embedding_model = SentenceTransformer(str(self.settings.embeddings_model_name))

            # Restore original environment variable
            if original_st_home:
                os.environ["SENTENCE_TRANSFORMERS_HOME"] = original_st_home
            else:
                del os.environ["SENTENCE_TRANSFORMERS_HOME"]

            logger.info("SentenceTransformer model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load SentenceTransformer model: {e}", exc_info=True)
            self.embedding_model = None
            raise

    async def _load_faiss_data(self):
        """Load existing FAISS index and metadata or create new ones."""
        if self.settings.faiss_index_path.exists() and self.settings.faiss_metadata_path.exists():
            try:
                logger.info(f"Loading FAISS index from {self.settings.faiss_index_path}")
                self.faiss_index = faiss.read_index(str(self.settings.faiss_index_path))

                logger.info(f"Loading FAISS metadata from {self.settings.faiss_metadata_path}")
                with open(self.settings.faiss_metadata_path) as f:
                    loaded_metadata = json.load(f)
                    self.metadata_store = loaded_metadata.get("metadata", {})
                    self.next_id_counter = loaded_metadata.get("next_id", 0)

                logger.info(
                    f"FAISS data loaded. Index size: {self.faiss_index.ntotal if self.faiss_index else 0}. Next ID: {self.next_id_counter}"
                )

                # Check dimension compatibility
                if self.faiss_index and self.faiss_index.d != self.settings.embeddings_model_dimensions:
                    logger.warning(
                        f"Loaded FAISS index dimension ({self.faiss_index.d}) differs from expected ({self.settings.embeddings_model_dimensions}). Re-initializing."
                    )
                    self._initialize_new_index()

            except Exception as e:
                logger.error(f"Error loading FAISS data: {e}. Re-initializing.", exc_info=True)
                self._initialize_new_index()
        else:
            logger.info("FAISS index or metadata not found. Initializing new.")
            self._initialize_new_index()

    def _initialize_new_index(self):
        """Initialize a new FAISS index."""
        self.faiss_index = faiss.IndexIDMap(faiss.IndexFlatL2(self.settings.embeddings_model_dimensions))
        self.metadata_store = {}
        self.next_id_counter = 0

    async def _save_data(self):
        """Save FAISS index and metadata to disk."""
        if self.faiss_index is None:
            logger.error("FAISS index is not initialized. Cannot save.")
            return

        try:
            # Ensure directory exists
            self.settings.servers_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"Saving FAISS index to {self.settings.faiss_index_path} (Size: {self.faiss_index.ntotal})")
            faiss.write_index(self.faiss_index, str(self.settings.faiss_index_path))

            logger.info(f"Saving FAISS metadata to {self.settings.faiss_metadata_path}")
            with open(self.settings.faiss_metadata_path, "w") as f:
                json.dump(
                    {"metadata": self.metadata_store, "next_id": self.next_id_counter},
                    f,
                    indent=2,
                    cls=_PydanticAwareJSONEncoder,
                )

            logger.info("FAISS data saved successfully.")
        except Exception as e:
            logger.error(f"Error saving FAISS data: {e}", exc_info=True)

    def _get_text_for_embedding(self, server_info: dict[str, Any]) -> str:
        """Prepare text string from server info (including tools) for embedding."""
        name = server_info.get("server_name", "")
        description = server_info.get("description", "")
        tags = server_info.get("tags", [])
        tag_string = ", ".join(tags)
        tool_list = server_info.get("tool_list") or []
        tool_snippets = []
        for tool in tool_list:
            tool_name = tool.get("name", "")
            parsed_description = tool.get("parsed_description", {}) or {}
            tool_desc = parsed_description.get("main") or tool.get("description", "")
            tool_args = parsed_description.get("args", "")
            snippet = f"Tool: {tool_name}. Description: {tool_desc}. Args: {tool_args}"
            tool_snippets.append(snippet.strip())

        tools_section = "\n".join(tool_snippets)
        return (f"Name: {name}\nDescription: {description}\nTags: {tag_string}\nTools:\n{tools_section}").strip()

    def _get_text_for_agent(self, agent_card) -> str:
        """
        DEPRECATED: Prepare text string from agent card for embedding.

        Legacy file-based agent support has been removed.
        """
        logger.warning("_get_text_for_agent called but legacy agent support has been removed.")
        return ""

    async def add_or_update_service(self, service_path: str, server_info: dict[str, Any], is_enabled: bool = False):
        """Add or update a service in the FAISS index."""
        if self.embedding_model is None or self.faiss_index is None:
            logger.error("Embedding model or FAISS index not initialized. Cannot add/update service in FAISS.")
            return

        logger.info(f"Attempting to add/update service '{service_path}' in FAISS.")
        text_to_embed = self._get_text_for_embedding(server_info)

        current_faiss_id = -1
        needs_new_embedding = True

        existing_entry = self.metadata_store.get(service_path)

        if existing_entry:
            current_faiss_id = existing_entry["id"]
            if existing_entry.get("text_for_embedding") == text_to_embed:
                needs_new_embedding = False
                logger.info(
                    f"Text for embedding for '{service_path}' has not changed. Will update metadata store only if server_info differs."
                )
            else:
                logger.info(f"Text for embedding for '{service_path}' has changed. Re-embedding required.")
        else:
            # New service
            current_faiss_id = self.next_id_counter
            self.next_id_counter += 1
            logger.info(f"New service '{service_path}'. Assigning new FAISS ID: {current_faiss_id}.")
            needs_new_embedding = True

        if needs_new_embedding:
            try:
                # Run model encoding in a separate thread
                embedding = await asyncio.to_thread(self.embedding_model.encode, [text_to_embed])
                embedding_np = np.array([embedding[0]], dtype=np.float32)

                ids_to_remove = np.array([current_faiss_id])
                if existing_entry:
                    try:
                        num_removed = self.faiss_index.remove_ids(ids_to_remove)
                        if num_removed > 0:
                            logger.info(
                                f"Removed {num_removed} old vector(s) for FAISS ID {current_faiss_id} ({service_path})."
                            )
                        else:
                            logger.info(
                                f"No old vector found for FAISS ID {current_faiss_id} ({service_path}) during update, or ID not in index."
                            )
                    except Exception as e_remove:
                        logger.warning(
                            f"Issue removing FAISS ID {current_faiss_id} for {service_path}: {e_remove}. Proceeding to add."
                        )

                self.faiss_index.add_with_ids(embedding_np, np.array([current_faiss_id]))
                logger.info(f"Added/Updated vector for '{service_path}' with FAISS ID {current_faiss_id}.")
            except Exception as e:
                logger.error(f"Error encoding or adding embedding for '{service_path}': {e}", exc_info=True)
                return

        # Update metadata store
        enriched_server_info = server_info.copy()
        enriched_server_info["is_enabled"] = is_enabled

        if (
            existing_entry is None
            or needs_new_embedding
            or existing_entry.get("full_server_info") != enriched_server_info
        ):
            self.metadata_store[service_path] = {
                "id": current_faiss_id,
                "text_for_embedding": text_to_embed,
                "full_server_info": enriched_server_info,
                "entity_type": server_info.get("entity_type", "mcp_server"),
            }
            logger.debug(f"Updated faiss_metadata_store for '{service_path}'.")
            await self._save_data()
        else:
            logger.debug(
                f"No changes to FAISS vector or enriched full_server_info for '{service_path}'. Skipping save."
            )

    async def remove_service(self, service_path: str):
        """Remove a service from the FAISS index and metadata store."""
        try:
            # Check if service exists in metadata
            if service_path not in self.metadata_store:
                logger.warning(f"Service '{service_path}' not found in FAISS metadata store")
                return

            # Get the FAISS ID for this service
            service_id = self.metadata_store[service_path].get("id")
            if service_id is not None and self.faiss_index:
                # Remove from FAISS index
                # Note: FAISS doesn't support direct removal, but we can remove from metadata
                # The vector will remain in the index but won't be accessible via metadata
                logger.info(f"Removing service '{service_path}' with FAISS ID {service_id} from index")

            # Remove from metadata store
            del self.metadata_store[service_path]
            logger.info(f"Removed service '{service_path}' from FAISS metadata store")

            # Save the updated metadata
            await self._save_data()

        except Exception as e:
            logger.error(f"Failed to remove service '{service_path}' from FAISS: {e}", exc_info=True)

    async def add_or_update_agent(
        self,
        agent_path: str,
        agent_card,
        is_enabled: bool = False,
    ) -> None:
        """
        DEPRECATED: Add or update an agent in the FAISS index.

        Legacy file-based agent support has been removed.
        Use A2A Agent Management V1 API (/api/v1/agents) instead.
        """
        logger.warning(
            f"add_or_update_agent called for '{agent_path}' but legacy agent support has been removed. "
            "Use A2A Agent Management V1 API."
        )
        return

    async def remove_agent(self, agent_path: str) -> None:
        """
        DEPRECATED: Remove an agent from the FAISS index and metadata store.

        Legacy file-based agent support has been removed.
        """
        logger.warning(f"remove_agent called for '{agent_path}' but legacy agent support has been removed.")
        return

    async def search_agents(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for agents in the FAISS index."""
        results = await self.search_mixed(
            query=query,
            entity_types=["a2a_agent"],
            max_results=max_results,
        )
        return results.get("agents", [])

    async def add_or_update_entity(
        self,
        entity_path: str,
        entity_info: dict[str, Any],
        entity_type: str,
        is_enabled: bool = False,
    ) -> None:
        """
        Wrapper method for adding or updating an entity.

        Routes entities to appropriate methods based on entity_type.
        """
        if entity_type == "a2a_agent":
            # Legacy file-based agent support removed
            logger.warning(
                f"Legacy agent '{entity_path}' cannot be added to FAISS - "
                "file-based agent service has been removed. Use A2A Agent Management V1 API."
            )
            return
        elif entity_type == "mcp_server":
            await self.add_or_update_service(entity_path, entity_info, is_enabled)

    async def remove_entity(
        self,
        entity_path: str,
    ) -> None:
        """
        Wrapper method for removing an entity.

        Attempts to remove as agent first, then server.
        """
        try:
            await self.remove_agent(entity_path)
        except Exception:
            try:
                await self.remove_service(entity_path)
            except Exception as e:
                logger.warning(f"Could not remove entity {entity_path}: {e}")

    async def search_entities(
        self,
        query: str,
        entity_types: list[str] | None = None,
        enabled_only: bool = False,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Wrapper method for searching entities.

        Searches both agents and servers, returns list of matching entities.
        """
        if entity_types is None:
            entity_types = ["a2a_agent", "mcp_server", "tool"]

        results = await self.search_mixed(
            query=query,
            entity_types=entity_types,
            max_results=max_results,
        )

        combined: list[dict[str, Any]] = []
        requested = set(entity_types)

        if "agents" in results and "a2a_agent" in requested:
            for agent in results["agents"]:
                if enabled_only and not agent.get("is_enabled", False):
                    continue
                combined.append(agent)

        if "servers" in results and "mcp_server" in requested:
            for server in results["servers"]:
                if enabled_only and not server.get("is_enabled", False):
                    continue
                combined.append(server)

        if "tools" in results and "tool" in requested:
            combined.extend(results["tools"])

        return combined[:max_results]

    def _distance_to_relevance(self, distance: float) -> float:
        """Convert FAISS L2 distance to a normalized relevance score (0-1)."""
        try:
            relevance = 1.0 / (1.0 + float(distance))
            return max(0.0, min(1.0, relevance))
        except Exception:
            return 0.0

    def _extract_matching_tools(self, query: str, server_info: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool matches using simple keyword overlap."""
        tools = server_info.get("tool_list") or []
        if not tools:
            return []

        tokens = [token for token in re.split(r"\W+", query.lower()) if token]
        if not tokens:
            return []

        matches: list[tuple[float, dict[str, Any]]] = []
        for tool in tools:
            tool_name = tool.get("name", "")
            parsed_description = tool.get("parsed_description", {}) or {}
            tool_desc = (
                parsed_description.get("main") or tool.get("description") or parsed_description.get("summary") or ""
            )
            tool_args = parsed_description.get("args", "")
            searchable_text = f"{tool_name} {tool_desc} {tool_args}".lower()
            if not searchable_text.strip():
                continue

            matches_found = sum(1 for token in tokens if token in searchable_text)
            if matches_found == 0:
                continue

            coverage = matches_found / len(tokens)
            matches.append(
                (
                    coverage,
                    {
                        "tool_name": tool_name,
                        "description": tool_desc,
                        "match_context": (tool_desc or tool_args or "")[:180],
                        "schema": tool.get("schema", {}),
                        "raw_score": coverage,
                    },
                )
            )

        matches.sort(key=lambda item: item[0], reverse=True)
        return [match for _, match in matches]

    async def search_mixed(
        self,
        query: str,
        entity_types: list[str] | None = None,
        max_results: int = 20,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Run a semantic search across MCP servers, their tools, and A2A agents.

        Args:
            query: Natural language query text
            entity_types: Optional list of entity filters ("mcp_server", "tool", "a2a_agent")
            max_results: Maximum results to return per entity collection

        Returns:
            Dict with "servers", "tools", and "agents" result lists
        """
        if not query or not query.strip():
            raise ValueError("Query text is required for semantic search")

        if self.embedding_model is None or self.faiss_index is None:
            raise RuntimeError("FAISS search service is not initialized")

        max_results = max(1, min(max_results, 50))
        requested_entity_types = set(entity_types or ["mcp_server", "tool", "a2a_agent"])
        allowed_entity_types = {"mcp_server", "tool", "a2a_agent"}
        entity_filter = requested_entity_types & allowed_entity_types
        if not entity_filter:
            entity_filter = allowed_entity_types

        total_vectors = self.faiss_index.ntotal if self.faiss_index else 0
        if total_vectors == 0:
            return {"servers": [], "tools": [], "agents": []}

        top_k = min(max_results, total_vectors)
        query_embedding = await asyncio.to_thread(self.embedding_model.encode, [query.strip()])
        query_np = np.array([query_embedding[0]], dtype=np.float32)

        distances, indices = self.faiss_index.search(query_np, top_k)
        distance_row = distances[0]
        id_row = indices[0]

        id_to_path = {entry.get("id"): path for path, entry in self.metadata_store.items()}

        server_results: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        agent_results: list[dict[str, Any]] = []

        for distance, faiss_id in zip(distance_row, id_row, strict=False):
            if faiss_id == -1:
                continue

            path = id_to_path.get(int(faiss_id))
            if not path:
                continue

            metadata_entry = self.metadata_store.get(path, {})
            entity_type = metadata_entry.get("entity_type", "mcp_server")
            relevance = self._distance_to_relevance(distance)

            if entity_type == "mcp_server":
                server_info = metadata_entry.get("full_server_info", {})
                if not server_info:
                    continue

                match_context = (
                    server_info.get("description") or ", ".join(server_info.get("tags", [])) or server_info.get("path")
                )

                matching_tools: list[dict[str, Any]] = []
                if "tool" in entity_filter:
                    matching_tools = self._extract_matching_tools(query, server_info)[:5]

                if "mcp_server" in entity_filter:
                    server_results.append(
                        {
                            "entity_type": "mcp_server",
                            "path": path,
                            "server_name": server_info.get("server_name", path.strip("/")),
                            "description": server_info.get("description", ""),
                            "tags": server_info.get("tags", []),
                            "num_tools": server_info.get("num_tools", 0),
                            "is_enabled": server_info.get("is_enabled", False),
                            "relevance_score": relevance,
                            "match_context": match_context,
                            "matching_tools": [
                                {
                                    "tool_name": tool.get("tool_name", ""),
                                    "description": tool.get("description", ""),
                                    "relevance_score": min(1.0, (relevance + tool.get("raw_score", 0)) / 2),
                                    "match_context": tool.get("match_context", ""),
                                }
                                for tool in matching_tools
                            ],
                        }
                    )

                if "tool" in entity_filter and matching_tools:
                    for tool in matching_tools:
                        tool_results.append(
                            {
                                "entity_type": "tool",
                                "server_path": path,
                                "server_name": server_info.get("server_name", path.strip("/")),
                                "tool_name": tool.get("tool_name", ""),
                                "description": tool.get("description", ""),
                                "match_context": tool.get("match_context", ""),
                                "relevance_score": min(1.0, (relevance + tool.get("raw_score", 0)) / 2),
                            }
                        )

            elif entity_type == "a2a_agent":
                if "a2a_agent" not in entity_filter:
                    continue

                agent_card = metadata_entry.get("full_agent_card", {})
                if not agent_card:
                    continue

                skills = [skill.get("name") for skill in agent_card.get("skills", []) if isinstance(skill, dict)]
                match_context = (
                    agent_card.get("description") or ", ".join(skills) or ", ".join(agent_card.get("tags", []))
                )

                agent_results.append(
                    {
                        "entity_type": "a2a_agent",
                        "path": path,
                        "agent_name": agent_card.get("name", path.strip("/")),
                        "description": agent_card.get("description", ""),
                        "tags": agent_card.get("tags", []),
                        "skills": skills,
                        "visibility": agent_card.get("visibility", "public"),
                        "trust_level": agent_card.get("trust_level"),
                        "is_enabled": agent_card.get("is_enabled", False),
                        "relevance_score": relevance,
                        "match_context": match_context,
                        "agent_card": agent_card,
                    }
                )

        server_results.sort(key=lambda item: item["relevance_score"], reverse=True)
        tool_results.sort(key=lambda item: item["relevance_score"], reverse=True)
        agent_results.sort(key=lambda item: item["relevance_score"], reverse=True)

        return {
            "servers": server_results[:max_results],
            "tools": tool_results[:max_results],
            "agents": agent_results[:max_results],
        }

    async def search(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search for services using FAISS semantic search and/or tag filtering.

        Note: This is a simplified interface. The full implementation in mcpgw/server.py
        has more sophisticated ranking logic. This is mainly for compatibility.
        """
        if not query and not tags:
            logger.warning("Search called with no query or tags")
            return []

        results = []

        # Simple tag filtering
        if tags:
            for service_path, entry in self.metadata_store.items():
                server_info = entry.get("full_server_info", {})
                service_tags = server_info.get("tags", [])

                # Check if all requested tags are present
                if all(tag in service_tags for tag in tags):
                    results.append({"service_path": service_path, "server_info": server_info, "score": 1.0})

        # Semantic search with FAISS
        elif query and self.embedding_model and self.faiss_index:
            try:
                # Encode query
                query_embedding = await asyncio.to_thread(self.embedding_model.encode, [query])
                query_embedding_np = np.array(query_embedding, dtype=np.float32)

                # Search FAISS
                distances, faiss_ids = await asyncio.to_thread(
                    self.faiss_index.search,
                    query_embedding_np,
                    min(top_k, self.faiss_index.ntotal) if self.faiss_index.ntotal > 0 else 1,
                )

                # Convert results
                for distance, faiss_id in zip(distances[0], faiss_ids[0], strict=False):
                    if faiss_id < 0:  # Invalid ID
                        continue

                    # Find service by FAISS ID
                    for service_path, entry in self.metadata_store.items():
                        if entry["id"] == int(faiss_id):
                            results.append(
                                {
                                    "service_path": service_path,
                                    "server_info": entry.get("full_server_info", {}),
                                    "score": float(1.0 / (1.0 + distance)),  # Convert distance to similarity
                                }
                            )
                            break
            except Exception as e:
                logger.error(f"FAISS search failed: {e}", exc_info=True)

        return results[:top_k]

    async def cleanup(self):
        """Save data and cleanup resources."""
        await self._save_data()
        logger.info("Embedded FAISS service cleanup complete")
