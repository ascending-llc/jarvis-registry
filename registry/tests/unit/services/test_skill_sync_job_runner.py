from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_job_runner import SkillSyncJobRunner


def _runner(job_service=None, execution_service=None):
    return SkillSyncJobRunner(
        job_service=job_service or MagicMock(),
        execution_service=execution_service or MagicMock(),
        lease_owner="worker-1",
    )


@pytest.mark.asyncio
async def test_recover_exhausted_job_releases_source_state():
    job = SimpleNamespace(id=PydanticObjectId())
    job_service = MagicMock(fail_next_exhausted_job=AsyncMock(return_value=job))
    execution_service = MagicMock(recover_exhausted_job=AsyncMock())
    runner = _runner(job_service, execution_service)

    await runner._recover_one_exhausted_job()

    execution_service.recover_exhausted_job.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_recover_exhausted_job_noop_when_none():
    job_service = MagicMock(fail_next_exhausted_job=AsyncMock(return_value=None))
    execution_service = MagicMock(recover_exhausted_job=AsyncMock())
    runner = _runner(job_service, execution_service)

    await runner._recover_one_exhausted_job()

    execution_service.recover_exhausted_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_and_shutdown_delegate_to_leased_runner():
    job_service = MagicMock(
        fail_next_exhausted_job=AsyncMock(return_value=None),
        claim_next_job=AsyncMock(return_value=None),
    )
    runner = _runner(job_service)

    await runner.start()
    assert runner._runner._task is not None

    await runner.shutdown()
    assert runner._runner._task is None
