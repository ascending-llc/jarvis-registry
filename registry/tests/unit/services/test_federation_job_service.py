from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation_job_service import FederationJobService
from registry_pkgs.models.enums import FederationJobPhase, FederationJobStatus


def _make_job(status: FederationJobStatus = FederationJobStatus.PENDING):
    return SimpleNamespace(
        status=status,
        phase=FederationJobPhase.QUEUED,
        error=None,
        startedAt=None,
        finishedAt=None,
        save=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_get_job_scopes_lookup_to_federation(monkeypatch):
    service = FederationJobService()
    job_id = PydanticObjectId()
    federation_id = PydanticObjectId()
    expected_job = _make_job()
    find_one = AsyncMock(return_value=expected_job)
    monkeypatch.setattr(
        "registry.services.federation_job_service.FederationSyncJob.find_one",
        find_one,
    )

    result = await service.get_job(str(job_id), federation_id=federation_id)

    assert result is expected_job
    find_one.assert_awaited_once_with(
        {
            "_id": job_id,
            "federationId": federation_id,
        }
    )


@pytest.mark.asyncio
async def test_get_job_returns_none_for_invalid_id(monkeypatch):
    service = FederationJobService()
    find_one = AsyncMock()
    monkeypatch.setattr(
        "registry.services.federation_job_service.FederationSyncJob.find_one",
        find_one,
    )

    result = await service.get_job("not-an-object-id", federation_id=PydanticObjectId())

    assert result is None
    find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_has_active_jobs_queries_active_statuses_across_all_federations(monkeypatch):
    service = FederationJobService()
    find_one = AsyncMock(return_value=_make_job())
    monkeypatch.setattr("registry.services.federation_job_service.FederationSyncJob.find_one", find_one)

    result = await service.has_active_jobs()

    assert result is True
    # No federationId filter: the reindex drain cares about any in-flight sync.
    query = find_one.await_args.args[0]
    assert "federationId" not in query
    assert set(query["status"]["$in"]) == {FederationJobStatus.PENDING.value, FederationJobStatus.SYNCING.value}


@pytest.mark.asyncio
async def test_has_active_jobs_false_when_none_in_flight(monkeypatch):
    service = FederationJobService()
    monkeypatch.setattr(
        "registry.services.federation_job_service.FederationSyncJob.find_one", AsyncMock(return_value=None)
    )

    assert await service.has_active_jobs() is False


@pytest.mark.asyncio
async def test_list_active_jobs_returns_oldest_active_jobs_across_all_federations(monkeypatch):
    service = FederationJobService()
    jobs = [_make_job(), _make_job(FederationJobStatus.SYNCING)]
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=jobs)
    find = MagicMock(return_value=cursor)
    monkeypatch.setattr("registry.services.federation_job_service.FederationSyncJob.find", find)

    result = await service.list_active_jobs(limit=5)

    assert result == jobs
    query = find.call_args.args[0]
    assert "federationId" not in query
    assert set(query["status"]["$in"]) == {FederationJobStatus.PENDING.value, FederationJobStatus.SYNCING.value}
    # Oldest first, so an orphaned job is always among the ones reported.
    cursor.sort.assert_called_once_with([("createdAt", 1)])
    cursor.limit.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_mark_syncing_rejects_terminal_job_transition():
    service = FederationJobService()
    job = _make_job(FederationJobStatus.SUCCESS)

    with pytest.raises(ValueError, match="cannot transition to syncing"):
        await service.mark_syncing(job, FederationJobPhase.DISCOVERING)


@pytest.mark.asyncio
async def test_mark_failed_rejects_terminal_job_transition():
    service = FederationJobService()
    job = _make_job(FederationJobStatus.SUCCESS)

    with pytest.raises(ValueError, match="cannot transition to failed"):
        await service.mark_failed(job, FederationJobPhase.FAILED, "boom")


@pytest.mark.asyncio
async def test_mark_success_updates_terminal_fields():
    service = FederationJobService()
    job = _make_job(FederationJobStatus.SYNCING)

    result = await service.mark_success(job)

    assert result.status == FederationJobStatus.SUCCESS
    assert result.phase == FederationJobPhase.COMPLETED
    assert isinstance(result.finishedAt, datetime)
    job.save.assert_awaited_once()
