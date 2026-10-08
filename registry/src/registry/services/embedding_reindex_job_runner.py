"""App-scoped runner that claims and executes the singleton embedding-reindex job under a lease."""

from __future__ import annotations

from registry.services.embedding_reindex_execution_service import (
    _MAX_ATTEMPTS,
    EmbeddingReindexExecutionService,
)
from registry.services.embedding_reindex_job_service import EmbeddingReindexJobService
from registry_pkgs.database.embedding_reindex_job_repository import embedding_reindex_repository
from registry_pkgs.database.leased_job import Lease, LeasedJobRunner
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob


class EmbeddingReindexJobRunner:
    """Drive the app-scoped lifecycle of the persisted embedding-reindex job.

    A thin wrapper over :class:`LeasedJobRunner`: it supplies the claim, execute and error-recording
    callbacks and keeps the ``start``/``shutdown`` API the container uses. Claim, heartbeat and the
    execution/heartbeat coupling all live in the shared lease library.
    """

    def __init__(
        self,
        *,
        job_service: EmbeddingReindexJobService,
        execution_service: EmbeddingReindexExecutionService,
        lease_owner: str | None = None,
    ) -> None:
        self._execution_service = execution_service
        self._runner: LeasedJobRunner[EmbeddingReindexJob] = LeasedJobRunner(
            name="embedding-reindex-job-runner",
            claim=lambda owner: job_service.claim_job(owner=owner),
            execute=self._execute,
            repository=embedding_reindex_repository,
            on_execution_error=self._record_last_error,
            owner=lease_owner,
        )

    async def start(self) -> None:
        await self._runner.start()

    async def shutdown(self) -> None:
        await self._runner.shutdown()

    async def _execute(self, job: EmbeddingReindexJob, lease: Lease) -> None:
        if job.attempts > _MAX_ATTEMPTS:
            # Retries exhausted: finalize (COMPLETED if the switch already happened, else FAILED)
            # instead of sweeping again.
            await self._execution_service.finish_exhausted_job(job, lease)
            return
        await self._execution_service.run_claimed_job(job, lease)

    async def _record_last_error(self, job: EmbeddingReindexJob, lease: Lease, exc: Exception) -> None:
        """Best-effort: stamp the last error while we still own the lease (ignored if we don't)."""
        await embedding_reindex_repository.transition(lease, {"lastError": str(exc)})
