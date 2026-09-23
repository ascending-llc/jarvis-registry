from datetime import UTC, datetime

from beanie import Document
from pydantic import ConfigDict, Field
from pymongo import IndexModel

from .enums import EmbeddingReindexJobStatus


class EmbeddingReindexJob(Document):
    """A persisted signal that an embedding-model reindex is running.

    "Active" is derived from the lease (``status == RUNNING`` and
    ``leaseExpiresAt`` in the future) rather than a separate boolean, so a crashed
    job that stops renewing its lease self-clears instead of leaving the registry
    stuck in maintenance mode forever.
    """

    status: EmbeddingReindexJobStatus = EmbeddingReindexJobStatus.RUNNING
    leaseOwner: str | None = None
    leaseExpiresAt: datetime | None = None
    heartbeatAt: datetime | None = None
    startedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finishedAt: datetime | None = None
    error: str | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "embedding_reindex_jobs"
        indexes = [IndexModel([("status", 1), ("leaseExpiresAt", 1)])]

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
