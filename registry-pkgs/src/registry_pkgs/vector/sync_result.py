from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VectorSyncResult:
    """Unified result type for all vector sync operations."""

    indexed: int = 0
    failed: int = 0
    deleted: int = 0
    skipped: int = 0
    metadata_updated: int = 0
    version: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "indexed": self.indexed,
            "failed": self.failed,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "metadata_updated": self.metadata_updated,
            "version": self.version,
            "error": self.error,
        }

    def to_dict_mcp(self) -> dict:
        """MCP-compatible format — preserves the key names federation_sync_service expects."""
        return {
            "indexed_tools": self.indexed,
            "failed_tools": self.failed,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "version": self.version,
            "error": self.error,
        }
