"""Persistence, claiming, and triggering for the singleton embedding-reindex job."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId
from pymongo import ReturnDocument

from registry.core.config import Settings
from registry.core.vector_backend import smoke_test_embedding_model
from registry.services.federation_job_service import FederationJobService
from registry.services.model_gateway_selection_service import ModelGatewaySelectionService
from registry_pkgs.database.embedding_reindex_job_repository import get_active_embedding_reindex_job
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus
from registry_pkgs.models.federation_sync_job import FederationSyncJob
from registry_pkgs.models.model_gateway_selection import ModelGatewaySelection

logger = logging.getLogger(__name__)

# Long enough for any live pod's 1s claim poll to grab a freshly created job well before this
# expires unclaimed, but short enough that a crash before the runner starts self-clears the gate.
_INITIAL_CLAIM_WINDOW = timedelta(seconds=60)

# How many active federation jobs the 409 names; enough to spot an orphan, bounded so the message stays short.
_FEDERATION_JOBS_REPORTED = 5


def _describe_federation_jobs(jobs: list[FederationSyncJob]) -> str:
    """Render active federation jobs for the 409 detail: enough for an operator to find each one in Mongo."""
    return "; ".join(
        f"job {job.id} (federation {job.federationId}, status {job.status}, created {job.createdAt.isoformat()})"
        for job in jobs
    )


class EmbeddingReindexAlreadyRunningError(RuntimeError):
    """Raised when a reindex is already active and a second trigger is rejected."""


class EmbeddingReindexFederationSyncActiveError(RuntimeError):
    """Raised when a federation sync job is in flight, so a reindex would only stall on its drain."""


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
        federation_job_service: FederationJobService,
    ) -> None:
        self._settings = settings
        self._selection_service = selection_service
        self._federation_job_service = federation_job_service

    async def get_active_job(self) -> EmbeddingReindexJob | None:
        """Return the running job whose lease has not expired, else None."""
        return await get_active_embedding_reindex_job()

    async def trigger_reindex(self, model_source_id: str, *, updated_by: str | None) -> ModelGatewaySelection:
        """Smoke-test the target model, then create the running job. Returns the CURRENT selection.

        Nothing is persisted unless the model passes the pre-flight embed check. A federation sync job in
        flight is rejected up front: the executor would otherwise sweep the whole corpus, then hold writes
        while it waits for that job to drain, and a job orphaned by a pod restart never drains. The
        executor's own drain still covers a sync that starts between this check and the write gate
        closing. The job captures the
        live ``(model, generation)`` as its ``previous*`` pair; the executor commits the new generation
        with a compare-and-set on it, so the selection only changes when the job completes.

        Single-flight is by that CAS, not by this ``get_active_job`` check (which only gives a friendly
        409): two simultaneous triggers each sweep an isolated generation, one CAS wins, the other fails.
        """
        model_source = await self._selection_service.resolve_embedding_model_source(model_source_id)

        if await self.get_active_job() is not None:
            raise EmbeddingReindexAlreadyRunningError("An embedding-model reindex is already running")

        await self._ensure_no_federation_sync_active()

        try:
            await smoke_test_embedding_model(
                model_source,
                self._settings.vector_config,
                encryption_key=self._settings.encryption_key,
            )
        except Exception as exc:
            raise EmbeddingModelSmokeTestError(str(exc)) from exc

        selection = await get_model_gateway_selection(create_if_missing=True)
        now = datetime.now(UTC)
        await EmbeddingReindexJob(
            targetEmbeddingModelSourceId=model_source.id,
            previousEmbeddingModelSourceId=selection.embeddingModelSourceId if selection else None,
            previousCollectionGeneration=selection.embeddingCollectionGeneration if selection else None,
            requestedBy=updated_by,
            status=EmbeddingReindexJobStatus.RUNNING,
            leaseOwner=None,
            leaseExpiresAt=now + _INITIAL_CLAIM_WINDOW,
            startedAt=now,
        ).insert()
        logger.info("Embedding reindex job created for model source %s", model_source.id)

        # 202 body: the currently active selection, unchanged (it switches only on completion).
        return selection

    async def _ensure_no_federation_sync_active(self) -> None:
        """Raise if any federation sync job is PENDING/SYNCING, naming the jobs found."""
        active_jobs = await self._federation_job_service.list_active_jobs(limit=_FEDERATION_JOBS_REPORTED)
        if not active_jobs:
            return
        raise EmbeddingReindexFederationSyncActiveError(
            "Cannot start an embedding-model reindex while a federation sync is in flight: "
            f"{_describe_federation_jobs(active_jobs)}. Retry once it finishes; a job that never finishes "
            "was orphaned by a registry restart and must be marked FAILED in Mongo."
        )

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
                },
                # Every claim counts as an attempt; the runner gives up once this exceeds _MAX_ATTEMPTS.
                # NOT in the filter: a used-up job must still be claimable so it can be finalized FAILED.
                "$inc": {"attempts": 1},
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
