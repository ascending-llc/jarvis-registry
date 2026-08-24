import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_job_runner import SkillSyncJobRunner, _SkillSyncLeaseLostError


@pytest.mark.asyncio
async def test_execute_runs_claimed_job_and_stops_heartbeat():
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock()
    job_service.heartbeat = AsyncMock(return_value=True)
    sync_service = MagicMock()
    sync_service.run_claimed_job = AsyncMock()
    runner = SkillSyncJobRunner(
        job_service=job_service,
        execution_service=sync_service,
        lease_owner="worker-1",
    )

    await runner._execute(job)

    sync_service.run_claimed_job.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_recover_exhausted_job_releases_source_state():
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock()
    job_service.fail_next_exhausted_job = AsyncMock(return_value=job)
    sync_service = MagicMock()
    sync_service.recover_exhausted_job = AsyncMock()
    runner = SkillSyncJobRunner(
        job_service=job_service,
        execution_service=sync_service,
        lease_owner="worker-1",
    )

    await runner._recover_one_exhausted_job()

    sync_service.recover_exhausted_job.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_execute_cancels_work_when_lease_is_lost():
    job = SimpleNamespace(id=PydanticObjectId())
    sync_service = MagicMock()
    execution_started = asyncio.Event()
    execution_cancelled = asyncio.Event()

    async def _run_claimed_job(_job):
        execution_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            execution_cancelled.set()

    sync_service.run_claimed_job = _run_claimed_job
    runner = SkillSyncJobRunner(
        job_service=MagicMock(),
        execution_service=sync_service,
        lease_owner="worker-1",
    )
    runner._heartbeat = AsyncMock(side_effect=_SkillSyncLeaseLostError("lost"))

    with pytest.raises(_SkillSyncLeaseLostError, match="lost"):
        await runner._execute(job)

    assert execution_started.is_set()
    assert execution_cancelled.is_set()


@pytest.mark.asyncio
async def test_start_and_shutdown_own_runner_task():
    job_service = MagicMock()
    job_service.fail_next_exhausted_job = AsyncMock(return_value=None)
    job_service.claim_next_job = AsyncMock(return_value=None)
    runner = SkillSyncJobRunner(
        job_service=job_service,
        execution_service=MagicMock(),
        lease_owner="worker-1",
    )

    await runner.start()
    assert runner._task is not None

    await runner.shutdown()
    assert runner._task is None


@pytest.mark.asyncio
async def test_start_and_shutdown_are_idempotent():
    runner = SkillSyncJobRunner(
        job_service=MagicMock(),
        execution_service=MagicMock(),
        lease_owner="worker-1",
    )
    runner._run = AsyncMock()

    await runner.start()
    first_task = runner._task
    await runner.start()
    assert runner._task is first_task
    await runner.shutdown()
    await runner.shutdown()


@pytest.mark.asyncio
async def test_run_claims_and_executes_one_job_before_stop():
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock(
        fail_next_exhausted_job=AsyncMock(return_value=None),
        claim_next_job=AsyncMock(return_value=job),
    )
    runner = SkillSyncJobRunner(
        job_service=job_service,
        execution_service=MagicMock(),
        lease_owner="worker-1",
    )

    async def _execute(_job):
        runner._stop_event.set()

    runner._execute = AsyncMock(side_effect=_execute)

    await runner._run()

    runner._execute.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_run_recovers_from_iteration_error_and_retries_wait():
    job_service = MagicMock(fail_next_exhausted_job=AsyncMock(side_effect=RuntimeError("mongo unavailable")))
    runner = SkillSyncJobRunner(
        job_service=job_service,
        execution_service=MagicMock(),
        lease_owner="worker-1",
    )

    async def _stop_after_wait():
        runner._stop_event.set()

    runner._wait_for_next_poll = AsyncMock(side_effect=_stop_after_wait)

    await runner._run()

    runner._wait_for_next_poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_renews_until_lease_is_lost(monkeypatch):
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock(heartbeat=AsyncMock(side_effect=[True, False]))
    runner = SkillSyncJobRunner(
        job_service=job_service,
        execution_service=MagicMock(),
        lease_owner="worker-1",
    )
    monkeypatch.setattr("registry.services.skill_sync_job_runner.asyncio.sleep", AsyncMock())

    with pytest.raises(_SkillSyncLeaseLostError):
        await runner._heartbeat(job)

    assert job_service.heartbeat.await_count == 2
