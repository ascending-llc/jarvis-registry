from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.schemas.workflow_schedule_schemas import ScheduleUpdateRequest
from registry.services.workflow_schedule_service import WorkflowScheduleService


def _schedule(*, enabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        cron_expression="0 2 * * *",
        timezone="UTC",
        enabled=enabled,
        created_by=PydanticObjectId(),
        created_at=None,
        next_run_at=None,
        locked_until=None,
        save=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_update_schedule_rejects_explicit_null_cron() -> None:
    schedule = _schedule()
    service = WorkflowScheduleService(acl_service=AsyncMock())
    service._load_schedule = AsyncMock(return_value=schedule)
    service._require_schedule_permission = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await service.update_schedule(
            str(schedule.workflow_definition_id),
            str(schedule.id),
            ScheduleUpdateRequest(cron_expression=None),
            str(schedule.created_by),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "cron_expression cannot be null"


@pytest.mark.asyncio
async def test_update_enabled_schedule_recalculates_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = _schedule(enabled=True)
    service = WorkflowScheduleService(acl_service=AsyncMock())
    service._load_schedule = AsyncMock(return_value=schedule)
    service._require_schedule_permission = AsyncMock()
    expected_next_run = object()
    monkeypatch.setattr(
        "registry.services.workflow_schedule_service.calculate_next_run_at",
        lambda *_args: expected_next_run,
    )

    updated = await service.update_schedule(
        str(schedule.workflow_definition_id),
        str(schedule.id),
        ScheduleUpdateRequest(cron_expression="15 2 * * *"),
        str(schedule.created_by),
    )

    assert updated.cron_expression == "15 2 * * *"
    assert updated.next_run_at is expected_next_run
    schedule.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabling_schedule_clears_next_run_and_lease() -> None:
    schedule = _schedule(enabled=True)
    schedule.next_run_at = object()
    schedule.locked_until = object()
    service = WorkflowScheduleService(acl_service=AsyncMock())
    service._load_schedule = AsyncMock(return_value=schedule)
    service._require_schedule_permission = AsyncMock()

    updated = await service.toggle_schedule(
        str(schedule.workflow_definition_id),
        str(schedule.id),
        False,
        str(schedule.created_by),
    )

    assert updated.enabled is False
    assert updated.next_run_at is None
    assert updated.locked_until is None
    schedule.save.assert_awaited_once()
