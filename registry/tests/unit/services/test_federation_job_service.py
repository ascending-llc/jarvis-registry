from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
