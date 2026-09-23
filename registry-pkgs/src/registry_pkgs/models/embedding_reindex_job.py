from datetime import UTC, datetime

from beanie import Document, PydanticObjectId
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

    # The ModelSource the sweep re-embeds every document against; the executor
    # resolves it to build the job-local vector client and, on success, the new
    # live adapter.
    targetEmbeddingModelSourceId: PydanticObjectId
    # Who triggered the switch. The gateway selection is committed by the executor
    # only after a successful swap (so a failed/crashed reindex never leaves the
    # selection pointing at a model the index was never built with), and this
    # preserves the audit of that deferred commit.
    triggeredBy: str | None = None
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
        indexes = [
            IndexModel([("status", 1), ("leaseExpiresAt", 1)]),
            IndexModel(
                [("status", 1)],
                unique=True,
                partialFilterExpression={"status": EmbeddingReindexJobStatus.RUNNING.value},
                name="uniq_running_embedding_reindex_job",
            ),
        ]

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
