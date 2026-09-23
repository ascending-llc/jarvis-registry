"""App-scoped runner that claims and executes the singleton embedding-reindex job under a lease."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from registry.services.embedding_reindex_execution_service import EmbeddingReindexExecutionService
from registry.services.embedding_reindex_job_service import EmbeddingReindexJobService
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
# Longer than skill sync's 2 minutes: a full-corpus embedding sweep against a real provider API
# can reasonably take several minutes before the lease would otherwise need renewing.
_LEASE_DURATION = timedelta(minutes=5)
_HEARTBEAT_INTERVAL_SECONDS = 30.0


class _EmbeddingReindexLeaseLostError(RuntimeError):
    pass


class EmbeddingReindexJobRunner:
    """Drive the app-scoped lifecycle of the persisted embedding-reindex job.

    Polls for a claimable RUNNING job, couples each execution with lease heartbeats, cancels stale
    execution after lease loss, and stops both tasks on shutdown. Business logic (the sweep and
    terminal status) is delegated to :class:`EmbeddingReindexExecutionService`. A pod crash simply
    stops lease renewal; the next pod reclaims the job and restarts the sweep from scratch, which is
    safe because ``sync_to_vector_db`` is idempotent per document.
    """

    def __init__(
        self,
        *,
        job_service: EmbeddingReindexJobService,
        execution_service: EmbeddingReindexExecutionService,
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
        self._task = asyncio.create_task(self._run(), name="embedding-reindex-job-runner")

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
        """Claim one runnable job per poll and execute it serially."""
        while not self._stop_event.is_set():
            try:
                job = await self._job_service.claim_job(
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
                logger.exception("Embedding reindex job runner iteration failed")
                await self._wait_for_next_poll()

    async def _execute(self, job: EmbeddingReindexJob) -> None:
        """Run execution and lease renewal as a coupled lifetime; lease loss cancels execution."""
        execution_task = asyncio.create_task(
            self._execution_service.run_claimed_job(job),
            name=f"embedding-reindex-execution-{job.id}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(job),
            name=f"embedding-reindex-heartbeat-{job.id}",
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

    async def _heartbeat(self, job: EmbeddingReindexJob) -> None:
        """Renew ownership periodically and fail fast when MongoDB rejects the owner."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            renewed = await self._job_service.heartbeat(
                job_id=job.id,
                lease_owner=self._lease_owner,
                lease_duration=_LEASE_DURATION,
            )
            if not renewed:
                raise _EmbeddingReindexLeaseLostError(f"Lost lease for embedding reindex job {job.id}")

    async def _wait_for_next_poll(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=_POLL_INTERVAL_SECONDS)
        except TimeoutError:
            return
