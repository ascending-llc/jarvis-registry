"""Persistence, claiming, and triggering for the singleton embedding-reindex job."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

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
        """Validate + smoke-test the target model and create the running job.

        Nothing is written to Mongo unless the target model both validates and passes the pre-flight
        embed check, so a broken model source is rejected before the registry enters maintenance mode.
        The job is created directly in RUNNING with no lease owner: it already satisfies AS-1867's
        gate, so writes and searches are blocked the instant this commits, with no unguarded window.

        The gateway selection is deliberately NOT persisted here — the executor commits it only after
        a successful adapter swap. Committing it up front would let a failed or crashed reindex leave
        ``embeddingModelSourceId`` pointing at a model the weaviate index was never rebuilt with, so a
        restart would build that model against the old index. The 202 response therefore reflects the
        REQUESTED target (what will be in effect once the job completes), not a persisted change.

        Single-flight: the ``get_active_job()`` check gives a friendly 409 in the common case, and
        the partial unique index on ``status == "running"`` is the atomic backstop. Two truly
        simultaneous triggers can both pass the check, but only one insert wins — the other's
        duplicate-key error is mapped to 409. This guarantees at most one RUNNING job, so two
        concurrent sweeps can never write different embedding models into the same collection. A
        crashed job that stays RUNNING with an expired lease holds the slot only until a runner
        reclaims and terminalizes it (~1s), or, during a persistent outage, until the sweep can
        finally complete — a self-healing bound, since you cannot reindex during such an outage anyway.
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
        try:
            await EmbeddingReindexJob(
                targetEmbeddingModelSourceId=model_source.id,
                triggeredBy=updated_by,
                status=EmbeddingReindexJobStatus.RUNNING,
                leaseOwner=None,
                leaseExpiresAt=now + _INITIAL_CLAIM_WINDOW,
                startedAt=now,
            ).insert()
        except DuplicateKeyError as exc:
            # Lost the single-flight race to a concurrent trigger between the check above and here.
            raise EmbeddingReindexAlreadyRunningError("An embedding-model reindex is already running") from exc
        logger.info("Embedding reindex job created for model source %s", model_source.id)

        # Response only: reflect the requested target without persisting it (see docstring).
        current = await self._selection_service.get_selection_or_none()
        return ModelGatewaySelection.model_construct(
            defaultWorkflowModelSourceId=current.defaultWorkflowModelSourceId if current else None,
            embeddingModelSourceId=model_source.id,
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
