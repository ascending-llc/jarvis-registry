"""Persistence, claiming, and triggering for the singleton embedding-reindex job."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId
from pymongo import ReturnDocument

from registry.core.config import Settings
from registry.core.vector_backend import smoke_test_embedding_model
from registry.services.model_gateway_selection_service import ModelGatewaySelectionService
from registry_pkgs.database.embedding_reindex_job_repository import get_active_embedding_reindex_job
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus
from registry_pkgs.models.model_gateway_selection import ModelGatewaySelection

logger = logging.getLogger(__name__)

# Long enough for any live pod's 1s claim poll to grab a freshly created job well before this
# expires unclaimed, but short enough that a crash before the runner starts self-clears the gate.
_INITIAL_CLAIM_WINDOW = timedelta(seconds=60)


class EmbeddingReindexAlreadyRunningError(RuntimeError):
    """Raised when a reindex is already active and a second trigger is rejected."""


class EmbeddingModelSmokeTestError(RuntimeError):
    """Raised when the target embedding model fails its pre-flight embed check."""


class EmbeddingReindexJobService:
    """Own the singleton embedding-reindex job: trigger, claim, and lease heartbeat.

    ``find_one_and_update`` is the worker-concurrency boundary — competing pods cannot both
    observe the same job as claimable. Only ever one reindex job is of interest at a time, so
    there is no PENDING/SYNCING split or per-source scoping the way skill sync needs.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        selection_service: ModelGatewaySelectionService,
    ) -> None:
        self._settings = settings
        self._selection_service = selection_service

    async def get_active_job(self) -> EmbeddingReindexJob | None:
        """Return the running job whose lease has not expired, else None (AS-1867 gate query)."""
        return await get_active_embedding_reindex_job()

    async def trigger_reindex(self, model_source_id: str, *, updated_by: str | None) -> ModelGatewaySelection:
        """Validate + smoke-test the target model, create the running job, then persist the selection.

        Nothing is written to Mongo unless the target model both validates and passes the pre-flight
        embed check, so a broken model source is rejected before the registry enters maintenance mode.
        The job is created directly in RUNNING with no lease owner: it already satisfies AS-1867's
        gate, so writes and searches are blocked the instant this commits, with no unguarded window.

        The job is created BEFORE the selection is persisted so the two writes are all-or-nothing for a
        single request: if persisting the selection then fails, the job is deleted (compensating
        rollback) rather than left RUNNING pointing at a stale selection — which would swap the live
        adapter to the new model while the persisted selection still names the old one, diverging on
        the next restart.

        Concurrency note: the ``get_active_job()`` check is not atomic with the insert below, so two
        truly simultaneous triggers can both pass it and create two jobs. That is tolerated: the
        reindex is idempotent per document and the runner processes jobs serially. A status-based
        unique index is deliberately NOT used — it would let a crashed/poison RUNNING job (which
        AS-1867 keeps RUNNING but treats as inactive via its expired lease) block every future
        trigger. Strict single-flight would require reworking AS-1867's lease-based liveness model.
        """
        model_source = await self._selection_service.resolve_embedding_model_source(model_source_id)

        if await self.get_active_job() is not None:
            raise EmbeddingReindexAlreadyRunningError("An embedding-model reindex is already running")

        try:
            await smoke_test_embedding_model(
                model_source,
                self._settings.vector_config,
                encryption_key=self._settings.encryption_key,
            )
        except Exception as exc:
            raise EmbeddingModelSmokeTestError(str(exc)) from exc

        now = datetime.now(UTC)
        job = EmbeddingReindexJob(
            targetEmbeddingModelSourceId=model_source.id,
            status=EmbeddingReindexJobStatus.RUNNING,
            leaseOwner=None,
            leaseExpiresAt=now + _INITIAL_CLAIM_WINDOW,
            startedAt=now,
        )
        await job.insert()
        try:
            selection = await self._selection_service.set_embedding_model(model_source_id, updated_by=updated_by)
        except Exception:
            try:
                await job.delete()
            except Exception:
                logger.exception(
                    "Failed to roll back reindex job %s after selection persist failed; it is left RUNNING",
                    job.id,
                )
            raise
        logger.info("Embedding reindex job created for model source %s", model_source.id)
        return selection

    async def claim_job(self, *, lease_owner: str, lease_duration: timedelta) -> EmbeddingReindexJob | None:
        """Atomically claim an unclaimed or lease-expired RUNNING job and establish a renewable lease."""
        now = datetime.now(UTC)
        document = await EmbeddingReindexJob.get_pymongo_collection().find_one_and_update(
            {
                "status": EmbeddingReindexJobStatus.RUNNING.value,
                "$or": [{"leaseOwner": None}, {"leaseExpiresAt": {"$lte": now}}],
            },
            {
                "$set": {
                    "leaseOwner": lease_owner,
                    "leaseExpiresAt": now + lease_duration,
                    "heartbeatAt": now,
                    "updatedAt": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return EmbeddingReindexJob.model_validate(document) if document is not None else None

    async def heartbeat(self, *, job_id: PydanticObjectId, lease_owner: str, lease_duration: timedelta) -> bool:
        """Renew the lease only while the caller still owns the running job."""
        now = datetime.now(UTC)
        result = await EmbeddingReindexJob.get_pymongo_collection().update_one(
            {
                "_id": job_id,
                "status": EmbeddingReindexJobStatus.RUNNING.value,
                "leaseOwner": lease_owner,
            },
            {
                "$set": {
                    "leaseExpiresAt": now + lease_duration,
                    "heartbeatAt": now,
                    "updatedAt": now,
                }
            },
        )
        return result.modified_count == 1
