from datetime import UTC, datetime, timedelta

from registry_pkgs.database.leased_job import CAMEL_CASE_LEASE_FIELDS, LeasedRepository, LeaseSpec
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus

# Longer than skill sync's 2 minutes: a full-corpus embedding sweep against a real provider API can
# reasonably take several minutes before the lease would otherwise need renewing.
_LEASE_DURATION = timedelta(minutes=5)
_HEARTBEAT_INTERVAL = timedelta(seconds=30)

EMBEDDING_REINDEX_LEASE: LeaseSpec[EmbeddingReindexJob] = LeaseSpec(
    document=EmbeddingReindexJob,
    fields=CAMEL_CASE_LEASE_FIELDS,
    status_field="status",
    leased_statuses=frozenset({EmbeddingReindexJobStatus.RUNNING.value}),
    duration=_LEASE_DURATION,
    heartbeat_interval=_HEARTBEAT_INTERVAL,
)

embedding_reindex_repository: LeasedRepository[EmbeddingReindexJob] = LeasedRepository(EMBEDDING_REINDEX_LEASE)


async def get_active_embedding_reindex_job() -> EmbeddingReindexJob | None:
    """Return the running reindex job whose lease has not expired, else None.

    A job with an expired lease (a crashed reindex that stopped renewing) is
    excluded by the ``$gt: now`` filter, so the gate self-clears instead of
    blocking forever. Mirrors ``get_model_gateway_selection`` in shape/location.
    """
    document = await EmbeddingReindexJob.get_pymongo_collection().find_one(
        {
            "status": EmbeddingReindexJobStatus.RUNNING.value,
            "leaseExpiresAt": {"$gt": datetime.now(UTC)},
        }
    )
    return EmbeddingReindexJob.model_validate(document) if document is not None else None
