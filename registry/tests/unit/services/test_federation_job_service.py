from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
