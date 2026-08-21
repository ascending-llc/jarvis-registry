from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_job_service import SkillSyncJobService
from registry_pkgs.models.enums import (
    SkillSyncJobErrorCode,
    SkillSyncJobPhase,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncTriggerType,
)


def _make_job(status: SkillSyncJobStatus = SkillSyncJobStatus.PENDING):
    return SimpleNamespace(
        status=status,
        phase=SkillSyncJobPhase.QUEUED,
        errorCode=None,
        error=None,
        finishedAt=None,
        save=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_get_job_returns_none_for_invalid_id(monkeypatch):
    service = SkillSyncJobService()
    find_one = AsyncMock()
    monkeypatch.setattr("registry.services.skill_sync_job_service.SkillSyncJob.find_one", find_one)

    result = await service.get_job("not-an-object-id", source_id=PydanticObjectId())

    assert result is None
    find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_job_returns_job_when_found(monkeypatch):
    service = SkillSyncJobService()
    job_id = PydanticObjectId()
    source_id = PydanticObjectId()
    expected_job = _make_job()
    find_one = AsyncMock(return_value=expected_job)
    monkeypatch.setattr("registry.services.skill_sync_job_service.SkillSyncJob.find_one", find_one)

    result = await service.get_job(str(job_id), source_id=source_id)

    assert result is expected_job
    find_one.assert_awaited_once_with({"_id": job_id, "sourceId": source_id})


@pytest.mark.asyncio
async def test_get_active_job_queries_pending_and_syncing_statuses(monkeypatch):
    service = SkillSyncJobService()
    source_id = PydanticObjectId()
    expected_job = _make_job(SkillSyncJobStatus.SYNCING)
    find_one = AsyncMock(return_value=expected_job)
    monkeypatch.setattr("registry.services.skill_sync_job_service.SkillSyncJob.find_one", find_one)

    result = await service.get_active_job(source_id)

    assert result is expected_job
    find_one.assert_awaited_once_with(
        {
            "sourceId": source_id,
            "status": {"$in": [SkillSyncJobStatus.PENDING.value, SkillSyncJobStatus.SYNCING.value]},
        },
        sort=[("createdAt", -1)],
        session=None,
    )


@pytest.mark.asyncio
async def test_get_active_job_returns_none_when_no_active_job(monkeypatch):
    service = SkillSyncJobService()
    find_one = AsyncMock(return_value=None)
    monkeypatch.setattr("registry.services.skill_sync_job_service.SkillSyncJob.find_one", find_one)

    result = await service.get_active_job(PydanticObjectId())

    assert result is None


@pytest.mark.asyncio
async def test_create_job_raises_when_active_job_exists(monkeypatch):
    service = SkillSyncJobService()
    find_one = AsyncMock(return_value=_make_job())
    monkeypatch.setattr("registry.services.skill_sync_job_service.SkillSyncJob.find_one", find_one)

    with pytest.raises(ValueError, match="already has an active sync job"):
        await service.create_job(
            source_id=PydanticObjectId(),
            job_type=SkillSyncJobType.FULL_SYNC,
            trigger_type=SkillSyncTriggerType.MANUAL,
            triggered_by="user-1",
            request_snapshot={},
        )


class _FakeSkillSyncJob:
    """Stand-in for the Beanie ``SkillSyncJob`` document that skips collection init."""

    find_one = None  # patched per-test

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.insert = AsyncMock()


@pytest.mark.asyncio
async def test_create_job_inserts_new_job_when_no_active_job(monkeypatch):
    service = SkillSyncJobService()
    source_id = PydanticObjectId()
    find_one = AsyncMock(return_value=None)
    monkeypatch.setattr("registry.services.skill_sync_job_service.SkillSyncJob", _FakeSkillSyncJob)
    monkeypatch.setattr(_FakeSkillSyncJob, "find_one", find_one)

    job = await service.create_job(
        source_id=source_id,
        job_type=SkillSyncJobType.FULL_SYNC,
        trigger_type=SkillSyncTriggerType.MANUAL,
        triggered_by="user-1",
        request_snapshot={"foo": "bar"},
    )

    assert job.sourceId == source_id
    assert job.jobType == SkillSyncJobType.FULL_SYNC
    assert job.triggerType == SkillSyncTriggerType.MANUAL
    assert job.triggeredBy == "user-1"
    assert job.requestSnapshot == {"foo": "bar"}
    job.insert.assert_awaited_once_with(session=None)


@pytest.mark.asyncio
async def test_create_job_passes_session_through(monkeypatch):
    service = SkillSyncJobService()
    source_id = PydanticObjectId()
    session = object()
    find_one = AsyncMock(return_value=None)
    monkeypatch.setattr("registry.services.skill_sync_job_service.SkillSyncJob", _FakeSkillSyncJob)
    monkeypatch.setattr(_FakeSkillSyncJob, "find_one", find_one)

    job = await service.create_job(
        source_id=source_id,
        job_type=SkillSyncJobType.DELETE_SYNC,
        trigger_type=SkillSyncTriggerType.API,
        triggered_by="user-2",
        request_snapshot={},
        session=session,
    )

    find_one.assert_awaited_once_with(
        {
            "sourceId": source_id,
            "status": {"$in": [SkillSyncJobStatus.PENDING.value, SkillSyncJobStatus.SYNCING.value]},
        },
        sort=[("createdAt", -1)],
        session=session,
    )
    job.insert.assert_awaited_once_with(session=session)


@pytest.mark.asyncio
async def test_mark_not_implemented_sets_failed_state():
    service = SkillSyncJobService()
    job = _make_job(SkillSyncJobStatus.SYNCING)

    result = await service.mark_not_implemented(job)

    assert result.status == SkillSyncJobStatus.FAILED
    assert result.phase == SkillSyncJobPhase.FAILED
    assert result.errorCode == SkillSyncJobErrorCode.SYNC_NOT_IMPLEMENTED.value
    assert result.error == "Skill sync execution is not implemented yet"
    assert result.finishedAt is not None
    job.save.assert_awaited_once()
