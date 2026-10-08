from __future__ import annotations

from registry_pkgs.database.leased_job import LeasedJobRunner
from registry_pkgs.models.skill_sync_job import SkillSyncJob

from .skill_sync_execution_service import SkillSyncExecutionService
from .skill_sync_job_service import SkillSyncJobService, skill_sync_repository


class SkillSyncJobRunner:
    """Drive the app-scoped lifecycle of persisted skill-sync jobs.

    A thin wrapper over :class:`LeasedJobRunner`: before each poll it propagates one exhausted job
    back to its source, then claims and executes the next runnable job under a lease. Claim,
    heartbeat and the execution/heartbeat coupling live in the shared lease library.
    """

    def __init__(
        self,
        *,
        job_service: SkillSyncJobService,
        execution_service: SkillSyncExecutionService,
        lease_owner: str | None = None,
    ) -> None:
        self._job_service = job_service
        self._execution_service = execution_service
        self._runner: LeasedJobRunner[SkillSyncJob] = LeasedJobRunner(
            name="skill-sync-job-runner",
            claim=lambda owner: job_service.claim_next_job(owner=owner),
            execute=execution_service.run_claimed_job,
            repository=skill_sync_repository,
            before_poll=self._recover_one_exhausted_job,
            owner=lease_owner,
        )

    async def start(self) -> None:
        await self._runner.start()

    async def shutdown(self) -> None:
        await self._runner.shutdown()

    async def _recover_one_exhausted_job(self) -> None:
        """Propagate terminal retry exhaustion from the job record back to its source."""
        job = await self._job_service.fail_next_exhausted_job()
        if job is not None:
            await self._execution_service.recover_exhausted_job(job)
