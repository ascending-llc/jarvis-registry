from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from registry_pkgs.models.skill_sync_job import SkillSyncJob

from .skill_sync_execution_service import SkillSyncExecutionService
from .skill_sync_job_service import SkillSyncJobService

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
_LEASE_DURATION = timedelta(minutes=2)
_HEARTBEAT_INTERVAL_SECONDS = 30.0


class _SkillSyncLeaseLostError(RuntimeError):
    pass


class SkillSyncJobRunner:
    """Drive the app-scoped lifecycle of persisted skill-sync jobs.

    The runner owns when and by which Registry instance a job executes: it polls, claims
    jobs, couples each execution with lease heartbeats, cancels stale execution after lease
    loss, and stops both tasks during shutdown. Business phases and terminal job/source
    states are delegated to :class:`SkillSyncExecutionService`.
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
        self._lease_owner = lease_owner or f"registry-{uuid4()}"
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start exactly one polling task for this app-scoped runner."""
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="skill-sync-job-runner")

    async def shutdown(self) -> None:
        """Stop polling and cancel any in-process execution; safe when never started."""
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _run(self) -> None:
        """Recover exhausted jobs, claim one runnable job, and execute it serially."""
        while not self._stop_event.is_set():
            try:
                await self._recover_one_exhausted_job()
                job = await self._job_service.claim_next_job(
                    lease_owner=self._lease_owner,
                    lease_duration=_LEASE_DURATION,
                )
                if job is None:
                    await self._wait_for_next_poll()
                    continue
                await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Skill sync job runner iteration failed")
                await self._wait_for_next_poll()

    async def _execute(self, job: SkillSyncJob) -> None:
        """Run job execution and lease renewal as a coupled lifetime.

        If heartbeat detects lost ownership, awaiting it raises and the ``finally`` block
        cancels the old execution before another worker can safely continue the job.
        """
        execution_task = asyncio.create_task(
            self._execution_service.run_claimed_job(job),
            name=f"skill-sync-execution-{job.id}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(job),
            name=f"skill-sync-heartbeat-{job.id}",
        )
        try:
            done, _pending = await asyncio.wait(
                {execution_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                await heartbeat_task
            await execution_task
        finally:
            for task in (execution_task, heartbeat_task):
                if task.done():
                    continue
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _heartbeat(self, job: SkillSyncJob) -> None:
        """Renew ownership periodically and fail fast when MongoDB rejects the owner."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            renewed = await self._job_service.heartbeat(
                job_id=job.id,
                lease_owner=self._lease_owner,
                lease_duration=_LEASE_DURATION,
            )
            if not renewed:
                raise _SkillSyncLeaseLostError(f"Lost lease for skill sync job {job.id}")

    async def _recover_one_exhausted_job(self) -> None:
        """Propagate terminal retry exhaustion from the job record back to its source."""
        job = await self._job_service.fail_next_exhausted_job()
        if job is not None:
            await self._execution_service.recover_exhausted_job(job)

    async def _wait_for_next_poll(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=_POLL_INTERVAL_SECONDS)
        except TimeoutError:
            return
