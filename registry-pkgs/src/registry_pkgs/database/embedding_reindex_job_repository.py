from datetime import UTC, datetime

from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus


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
