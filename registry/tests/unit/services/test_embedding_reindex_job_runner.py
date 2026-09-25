import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.embedding_reindex_job_runner import (
    EmbeddingReindexJobRunner,
    _EmbeddingReindexLeaseLostError,
)

pytestmark = pytest.mark.asyncio


def _runner(**overrides):
    kwargs = {
        "job_service": MagicMock(),
        "execution_service": MagicMock(),
        "lease_owner": "worker-1",
    }
    kwargs.update(overrides)
    return EmbeddingReindexJobRunner(**kwargs)


async def test_execute_runs_claimed_job_and_stops_heartbeat():
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock(heartbeat=AsyncMock(return_value=True))
    execution_service = MagicMock(run_claimed_job=AsyncMock())
    runner = _runner(job_service=job_service, execution_service=execution_service)

    await runner._execute(job)

    execution_service.run_claimed_job.assert_awaited_once_with(job)


async def test_execute_cancels_work_when_lease_is_lost():
    job = SimpleNamespace(id=PydanticObjectId())
    execution_started = asyncio.Event()
    execution_cancelled = asyncio.Event()

    async def _run_claimed_job(_job):
        execution_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            execution_cancelled.set()

    execution_service = MagicMock(run_claimed_job=_run_claimed_job)
    runner = _runner(execution_service=execution_service)
    runner._heartbeat = AsyncMock(side_effect=_EmbeddingReindexLeaseLostError("lost"))

    with pytest.raises(_EmbeddingReindexLeaseLostError, match="lost"):
        await runner._execute(job)

    assert execution_started.is_set()
    assert execution_cancelled.is_set()


async def test_run_claims_and_executes_one_job_before_stop():
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock(claim_job=AsyncMock(return_value=job))
    runner = _runner(job_service=job_service)

    async def _execute(_job):
        runner._stop_event.set()

    runner._execute = AsyncMock(side_effect=_execute)

    await runner._run()

    runner._execute.assert_awaited_once_with(job)


async def test_run_recovers_from_iteration_error_and_retries_wait():
    job_service = MagicMock(claim_job=AsyncMock(side_effect=RuntimeError("mongo unavailable")))
    runner = _runner(job_service=job_service)

    async def _stop_after_wait():
        runner._stop_event.set()

    runner._wait_for_next_poll = AsyncMock(side_effect=_stop_after_wait)

    await runner._run()

    runner._wait_for_next_poll.assert_awaited_once()


async def test_heartbeat_renews_until_lease_is_lost(monkeypatch):
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock(heartbeat=AsyncMock(side_effect=[True, False]))
    runner = _runner(job_service=job_service)
    monkeypatch.setattr("registry.services.embedding_reindex_job_runner.asyncio.sleep", AsyncMock())

    with pytest.raises(_EmbeddingReindexLeaseLostError):
        await runner._heartbeat(job)

    assert job_service.heartbeat.await_count == 2


async def test_start_and_shutdown_own_runner_task():
    job_service = MagicMock(claim_job=AsyncMock(return_value=None))
    runner = _runner(job_service=job_service)

    await runner.start()
    assert runner._task is not None

    await runner.shutdown()
    assert runner._task is None
