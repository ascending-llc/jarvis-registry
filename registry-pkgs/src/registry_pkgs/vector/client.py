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
        self._initialized = False
        self._repositories = {}
        self._reindex_active_check: Callable[[], bool] | None = None

    def set_reindex_active_check(self, check: Callable[[], bool] | None) -> None:
        """Wire the embedding-reindex gate so every adapter access can honor it.

        Accepts a plain callable (typically ``EmbeddingMaintenanceWatcher.is_active``)
        rather than the watcher itself, keeping registry-pkgs free of a dependency
        on the registry workspace.
        """
        self._reindex_active_check = check

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
            self._initialized = False
            self._repositories.clear()

            logger.info("Database client closed")

        except Exception as e:
            logger.error(f"Error closing database client: {e}")

    def swap_adapter(self, new_adapter: VectorStoreAdapter, new_config: BackendConfig) -> VectorStoreAdapter:
        """Atomically replace the live adapter in place; return the old one for the caller to close.

        Every repository resolves ``db_client.adapter`` freshly on each call, so mutating
        ``_adapter`` on this one shared instance is visible everywhere immediately — including
        repositories cached elsewhere that hold a reference to this ``DatabaseClient``. A single
        attribute assignment is atomic under asyncio's single-threaded loop (no ``await`` between
        the read and the write), so no lock is needed. The caller owns closing the returned adapter.
        """
        old_adapter = self._adapter
        self._adapter = new_adapter
        self._config = new_config
        return old_adapter

    def is_initialized(self) -> bool:
        """Check if the client is initialized."""
        return self._initialized and self._adapter is not None

    @property
    def adapter(self) -> VectorStoreAdapter:
        """
        Get direct access to the underlying adapter.

        For advanced users who need low-level Document operations.

        """
        self._ensure_initialized()
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
        """Ensure client is initialized and no embedding reindex is in progress.

        This is the single chokepoint every repository write and search passes
        through (``Repository.adapter`` resolves ``db_client.adapter`` freshly on
        every call), so gating here also covers any caller that reaches a
        repository directly rather than through a gated service method.
        """
        if not self._initialized:
            raise RuntimeError("Database client not initialized. Call initialize() first.")
        if self._reindex_active_check is not None and self._reindex_active_check():
            raise EmbeddingReindexInProgressException("An embedding-model reindex is in progress")


def create_database_client(config: BackendConfig) -> DatabaseClient:
    """Create and initialize a database client."""
    client = DatabaseClient()
    client.initialize(config)
    return client
