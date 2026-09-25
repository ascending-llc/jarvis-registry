"""Unified database client interface."""

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from ..core.exceptions import EmbeddingReindexInProgressException
from .adapters.adapter import VectorStoreAdapter
from .adapters.factory import VectorStoreFactory
from .config import BackendConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DatabaseClient:
    """
    Lightweight database manager.

    Core responsibilities:
    - Configuration management
    - Connection lifecycle (initialize/close)
    - Repository factory (for_model)
    - Direct adapter access for advanced users

    Recommended usage:
        db = create_database_client(config)
        tools_repo = db.for_model(McpTool)  # High-level Model API
        tools = tools_repo.search("query")

    Advanced usage:
        adapter = db.adapter  # Low-level Document API
        docs = adapter.similarity_search(collection_name="...", query="...")
    """

    def __init__(self, config: BackendConfig | None = None):
        """Initialize database client with optional configuration."""
        self._config = config
        self._adapter: VectorStoreAdapter | None = None
        # The (adapter, config) pair as ONE field, swapped atomically. A single-field load/store is
        # atomic under the GIL, so a reader that snapshots this can never pair an old adapter with a
        # new generation's collection name (repository ops run in to_thread workers, off the loop).
        self._live: tuple[VectorStoreAdapter, BackendConfig] | None = None
        self._initialized = False
        self._repositories = {}
        self._reindex_active_check: Callable[[], bool] | None = None

    def set_reindex_active_check(self, check: Callable[[], bool] | None) -> None:
        """Wire the reindex gate that blocks WRITES during a reindex.

        A plain callable (typically ``EmbeddingMaintenanceWatcher.is_active``) keeps registry-pkgs off
        the registry workspace. Gates ``write_adapter`` only — reads (``adapter``) are always allowed.
        """
        self._reindex_active_check = check

    @property
    def collection_generation(self) -> str | None:
        """The collection generation this client is currently built for (None == legacy generation 0).

        The watcher compares this against the selection's active generation to decide when to swap.
        """
        return self._config.collection_generation if self._config else None

    def collection_name_for(self, base_name: str) -> str:
        """Resolve a base name to this client's current generation, re-read on every call so a
        ``swap_adapter`` onto a new generation takes effect immediately for every repository."""
        generation = self._config.collection_generation if self._config else None
        return collection_name_for(base_name, generation)

    def initialize(self, config: BackendConfig | None = None) -> None:
        """
        Initialize database client with configuration.

        Args:
            config: Database configuration (uses instance config if not provided)

        Raises:
            ValueError: If no configuration is provided
            RuntimeError: If initialization fails
        """
        if self._initialized:
            logger.warning("Database client already initialized")
            return

        try:
            config = config or self._config
            if not config:
                raise ValueError("Configuration required for initialization")

            logger.info("Initializing database client...")

            # Create adapter through factory
            self._adapter = VectorStoreFactory.create_adapter(config)
            self._config = config
            self._live = (self._adapter, config)
            self._initialized = True

            logger.info(f"Database client initialized with {type(self._adapter).__name__}")

        except Exception as e:
            logger.error(f"Failed to initialize database client: {e}")
            raise

    def close(self) -> None:
        """Close database connection and clean up resources."""
        if not self._initialized:
            return

        try:
            logger.info("Closing database client...")

            if hasattr(self._adapter, "close"):
                self._adapter.close()
            elif hasattr(self._adapter, "__exit__"):
                self._adapter.__exit__(None, None, None)

            self._adapter = None
            self._live = None
            self._initialized = False
            self._repositories.clear()

            logger.info("Database client closed")

        except Exception as e:
            logger.error(f"Error closing database client: {e}")

    def swap_adapter(self, new_adapter: VectorStoreAdapter, new_config: BackendConfig) -> VectorStoreAdapter:
        """Replace the live adapter+config in place; return the old adapter for the caller to close.

        The ``(adapter, config)`` pair is published as one field (``_live``) in a single store, so an
        embedding search that reads it via :meth:`snapshot` always gets a consistent pair — it can
        never embed with the old model against the new generation's collection. The separate
        ``_adapter`` / ``_config`` fields are kept in sync for the other, generation-insensitive
        accessors (filter/get, lifecycle).
        """
        old_adapter = self._adapter
        self._live = (new_adapter, new_config)  # atomic publish of the consistent pair
        self._adapter = new_adapter
        self._config = new_config
        return old_adapter

    def snapshot(self) -> tuple[VectorStoreAdapter, BackendConfig]:
        """Atomically read the live ``(adapter, config)`` pair. Use this wherever the adapter's
        embedding model and the collection name must agree (near-text / hybrid search)."""
        self._ensure_initialized()
        return self._live

    def is_initialized(self) -> bool:
        """Check if the client is initialized."""
        return self._initialized and self._adapter is not None

    @property
    def adapter(self) -> VectorStoreAdapter:
        """Read adapter — NOT reindex-gated, so searches stay available during a reindex.
        Mutating ops must go through ``write_adapter``."""
        self._ensure_initialized()
        return self._adapter

    @property
    def write_adapter(self) -> VectorStoreAdapter:
        """Write adapter — the single chokepoint that raises while a reindex is active, so no write
        lands in a generation about to be superseded."""
        self._ensure_initialized()
        if self._reindex_active_check is not None and self._reindex_active_check():
            raise EmbeddingReindexInProgressException("An embedding-model reindex is in progress")
        return self._adapter

    def get_info(self) -> dict[str, Any]:
        """
        Get client information and status.

        Returns:
            Dictionary with client status and configuration
        """
        if not self._initialized:
            return {"initialized": False}

        info = {
            "initialized": True,
            "adapter_type": type(self._adapter).__name__,
            "default_collection": getattr(self._adapter, "_default_collection", "unknown"),
            "initialized_collections": getattr(self._adapter, "list_collections", lambda: [])(),
        }

        return info

    def _ensure_initialized(self) -> None:
        """Ensure the client is initialized. Reindex gating lives in ``write_adapter``, not here."""
        if not self._initialized:
            raise RuntimeError("Database client not initialized. Call initialize() first.")


def collection_name_for(base_name: str, generation: str | None) -> str:
    """Return the physical collection name for a base name and generation.

    ``generation is None`` -> the legacy base name (generation 0); otherwise ``f"{base}_{generation}"``.
    A free function so the reindex executor can name collections of a generation no live client is
    using yet (e.g. building the target generation before it becomes active).
    """
    return base_name if generation is None else f"{base_name}_{generation}"


def create_database_client(config: BackendConfig) -> DatabaseClient:
    """Create and initialize a database client."""
    client = DatabaseClient()
    client.initialize(config)
    return client
