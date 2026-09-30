from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus


async def transition_embedding_reindex_job(
    *,
    job_id: PydanticObjectId,
    lease_owner: str,
    set_fields: dict[str, Any],
    session: AsyncClientSession | None = None,
) -> bool:
    """Atomically apply ``set_fields`` to a job only while this pod still owns its RUNNING lease.

    The filter ``{_id, status: RUNNING, leaseOwner}`` is the single safety gate for AS-1868:
    ``status: RUNNING`` makes COMPLETED/FAILED final, and ``leaseOwner`` stops a pod whose lease was
    taken over from overwriting the real owner's state. Returns True iff exactly one document was
    updated; a False result means this pod no longer owns the job and must stop all writes for it.
    """
    result = await EmbeddingReindexJob.get_pymongo_collection().update_one(
        {"_id": job_id, "status": EmbeddingReindexJobStatus.RUNNING.value, "leaseOwner": lease_owner},
        {"$set": {**set_fields, "updatedAt": datetime.now(UTC)}},
        session=session,
    )
    return result.modified_count == 1


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
