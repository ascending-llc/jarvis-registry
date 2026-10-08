from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.embedding_reindex_job_runner import _MAX_ATTEMPTS, EmbeddingReindexJobRunner
from registry_pkgs.database.leased_job import Lease

pytestmark = pytest.mark.asyncio


def _lease(doc_id: PydanticObjectId) -> Lease:
    return Lease(doc_id=doc_id, owner="worker-1", token="tok", expires_at=datetime.now(UTC) + timedelta(minutes=5))


def _runner(**overrides):
    kwargs = {"job_service": MagicMock(), "execution_service": MagicMock(), "lease_owner": "worker-1"}
    kwargs.update(overrides)
    return EmbeddingReindexJobRunner(**kwargs)


async def test_execute_runs_claimed_job_with_lease():
    job = SimpleNamespace(id=PydanticObjectId(), attempts=1)
    execution_service = MagicMock(run_claimed_job=AsyncMock())
    runner = _runner(execution_service=execution_service)
    lease = _lease(job.id)

    await runner._execute(job, lease)

    execution_service.run_claimed_job.assert_awaited_once_with(job, lease)


async def test_execute_finalizes_an_exhausted_job_without_sweeping():
    job = SimpleNamespace(id=PydanticObjectId(), attempts=_MAX_ATTEMPTS + 1)
    execution_service = MagicMock(run_claimed_job=AsyncMock(), finish_exhausted_job=AsyncMock())
    runner = _runner(execution_service=execution_service)
    lease = _lease(job.id)

    await runner._execute(job, lease)

    execution_service.finish_exhausted_job.assert_awaited_once_with(job, lease)
    execution_service.run_claimed_job.assert_not_awaited()


async def test_record_last_error_writes_through_repository(monkeypatch):
    job = SimpleNamespace(id=PydanticObjectId(), attempts=1)
    runner = _runner()
    lease = _lease(job.id)
    transition = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "registry.services.embedding_reindex_job_runner.embedding_reindex_repository.transition", transition
    )

    await runner._record_last_error(job, lease, RuntimeError("weaviate down"))

    transition.assert_awaited_once_with(lease, {"lastError": "weaviate down"})


async def test_start_and_shutdown_delegate_to_leased_runner():
    job_service = MagicMock(claim_job=AsyncMock(return_value=None))
    runner = _runner(job_service=job_service)

    await runner.start()
    assert runner._runner._task is not None

    await runner.shutdown()
    assert runner._runner._task is None
