from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.workflow_schedule_schemas import ScheduleCreateRequest, ScheduleUpdateRequest
from registry.services import workflow_schedule_service
from registry.services.workflow_schedule_service import ScheduleAccess, WorkflowScheduleService


class _AsyncContext:
    def __init__(self, value=None) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _Session:
    async def start_transaction(self):
        return _AsyncContext()


def _patch_transaction(monkeypatch: pytest.MonkeyPatch) -> _Session:
    session = _Session()
    client = SimpleNamespace(start_session=lambda: _AsyncContext(session))
    monkeypatch.setattr(workflow_schedule_service.MongoDB, "get_client", lambda: client)
    return session


def _access(schedule: SimpleNamespace) -> ScheduleAccess:
    return ScheduleAccess(
        schedule=schedule,
        permissions=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=False),
    )


@pytest.mark.asyncio
async def test_create_schedule_grants_owner_in_same_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _patch_transaction(monkeypatch)
    workflow_id = PydanticObjectId()
    user_id = PydanticObjectId()
    acl_service = AsyncMock()
    service = WorkflowScheduleService(acl_service=acl_service)
    service._require_workflow_permission = AsyncMock(return_value=SimpleNamespace(enabled=True))

    class _FakeSchedule:
        def __init__(self, **kwargs) -> None:
            self.id = PydanticObjectId()
            self.insert = AsyncMock()
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr(workflow_schedule_service, "WorkflowSchedule", _FakeSchedule)

    access = await service.create_schedule(
        str(workflow_id),
        ScheduleCreateRequest(cron_expression="0 2 * * *"),
        str(user_id),
    )

    access.schedule.insert.assert_awaited_once_with(session=session)
    acl_service.grant_permission.assert_awaited_once()
    assert acl_service.grant_permission.await_args.kwargs["session"] is session
    assert acl_service.grant_permission.await_args.kwargs["resource_id"] == access.schedule.id
    assert access.permissions == ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)


@pytest.mark.asyncio
async def test_authorized_loader_checks_parent_and_child_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = _schedule()
    permissions = ResourcePermissions(VIEW=True)
    acl_service = AsyncMock()
    acl_service.check_user_permission.return_value = permissions
    service = WorkflowScheduleService(acl_service=acl_service)
    service._require_workflow_permission = AsyncMock(return_value=SimpleNamespace(enabled=True))
    service._load_schedule = AsyncMock(return_value=schedule)

    access = await service._load_authorized_schedule(
        str(schedule.workflow_definition_id),
        str(schedule.id),
        str(schedule.created_by),
        workflow_permission="VIEW",
        schedule_permission="VIEW",
    )

    assert access == ScheduleAccess(schedule=schedule, permissions=permissions)
    service._require_workflow_permission.assert_awaited_once()
    assert service._require_workflow_permission.await_args.args[2] == "VIEW"
    acl_service.check_user_permission.assert_awaited_once()
    assert acl_service.check_user_permission.await_args.kwargs["required_permission"] == "VIEW"


@pytest.mark.asyncio
async def test_list_schedules_filters_by_child_schedule_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    visible = _schedule()
    hidden = _schedule()
    hidden.workflow_definition_id = visible.workflow_definition_id
    user_id = visible.created_by
    acl_service = AsyncMock()
    acl_service.get_accessible_resource_ids.return_value = [str(visible.id), str(hidden.id)]
    acl_service.get_user_permissions_for_resources.return_value = {
        visible.id: ResourcePermissions(VIEW=True),
        hidden.id: ResourcePermissions(EDIT=True),
    }
    service = WorkflowScheduleService(acl_service=acl_service)
    service._require_workflow_permission = AsyncMock(return_value=SimpleNamespace(enabled=True))

    class _Query:
        def sort(self, *_args):
            return self

        async def to_list(self):
            return [visible, hidden]

    monkeypatch.setattr(workflow_schedule_service.WorkflowSchedule, "find", lambda *_args: _Query())

    result = await service.list_schedules(str(visible.workflow_definition_id), str(user_id))

    assert result == [ScheduleAccess(schedule=visible, permissions=ResourcePermissions(VIEW=True))]
    acl_service.get_user_permissions_for_resources.assert_awaited_once()


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
async def test_get_schedule_reuses_authorized_loader() -> None:
    schedule = _schedule()
    service = WorkflowScheduleService(acl_service=AsyncMock())
    access = _access(schedule)
    service._load_authorized_schedule = AsyncMock(return_value=access)

    result = await service.get_schedule(
        str(schedule.workflow_definition_id),
        str(schedule.id),
        str(schedule.created_by),
    )

    assert result is access
    service._load_authorized_schedule.assert_awaited_once_with(
        str(schedule.workflow_definition_id),
        str(schedule.id),
        str(schedule.created_by),
        workflow_permission="VIEW",
        schedule_permission="VIEW",
    )


@pytest.mark.asyncio
async def test_update_schedule_rejects_explicit_null_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = _schedule()
    _patch_transaction(monkeypatch)
    service = WorkflowScheduleService(acl_service=AsyncMock())
    service._load_authorized_schedule = AsyncMock(return_value=_access(schedule))

    with pytest.raises(HTTPException) as exc_info:
        await service.update_schedule(
            str(schedule.workflow_definition_id),
            str(schedule.id),
            ScheduleUpdateRequest(cron_expression=None),
            str(schedule.created_by),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "error": "invalid_parameter",
        "message": "cron_expression cannot be null",
    }


@pytest.mark.asyncio
async def test_update_enabled_schedule_recalculates_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = _schedule(enabled=True)
    _patch_transaction(monkeypatch)
    service = WorkflowScheduleService(acl_service=AsyncMock())
    service._load_authorized_schedule = AsyncMock(return_value=_access(schedule))
    expected_next_run = object()
    monkeypatch.setattr(
        "registry.services.workflow_schedule_service.calculate_next_run_at",
        lambda *_args: expected_next_run,
    )
    collection = SimpleNamespace()

    async def _find_one_and_update(_query, update, **_kwargs):
        for key, value in update["$set"].items():
            setattr(schedule, key, value)
        return schedule

    collection.find_one_and_update = AsyncMock(side_effect=_find_one_and_update)
    monkeypatch.setattr(workflow_schedule_service, "_schedule_collection", lambda: collection)
    monkeypatch.setattr(workflow_schedule_service.WorkflowSchedule, "model_validate", lambda document: document)

    updated = await service.update_schedule(
        str(schedule.workflow_definition_id),
        str(schedule.id),
        ScheduleUpdateRequest(cron_expression="15 2 * * *"),
        str(schedule.created_by),
    )

    assert updated.schedule.cron_expression == "15 2 * * *"
    assert updated.schedule.next_run_at is expected_next_run
    update = collection.find_one_and_update.await_args.args[1]["$set"]
    assert "locked_until" not in update
    assert "last_run_status" not in update


@pytest.mark.asyncio
async def test_disabling_schedule_clears_next_run_and_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = _schedule(enabled=True)
    schedule.next_run_at = object()
    schedule.locked_until = object()
    schedule.lease_token = "lease-1"
    _patch_transaction(monkeypatch)
    service = WorkflowScheduleService(acl_service=AsyncMock())
    service._load_authorized_schedule = AsyncMock(return_value=_access(schedule))
    collection = SimpleNamespace()

    async def _find_one_and_update(_query, update, **_kwargs):
        for key, value in update["$set"].items():
            setattr(schedule, key, value)
        return schedule

    collection.find_one_and_update = AsyncMock(side_effect=_find_one_and_update)
    monkeypatch.setattr(workflow_schedule_service, "_schedule_collection", lambda: collection)
    monkeypatch.setattr(workflow_schedule_service.WorkflowSchedule, "model_validate", lambda document: document)

    updated = await service.toggle_schedule(
        str(schedule.workflow_definition_id),
        str(schedule.id),
        False,
        str(schedule.created_by),
    )

    assert updated.schedule.enabled is False
    assert updated.schedule.next_run_at is None
    assert updated.schedule.locked_until is None
    assert updated.schedule.lease_token is None


@pytest.mark.asyncio
async def test_delete_schedule_rolls_back_when_acl_cleanup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = _schedule()
    session = _patch_transaction(monkeypatch)
    acl_service = AsyncMock()
    acl_service.delete_acl_entries_for_resource.return_value = 0
    service = WorkflowScheduleService(acl_service=acl_service)
    service._load_authorized_schedule = AsyncMock(return_value=_access(schedule))
    schedule.delete = AsyncMock()

    with pytest.raises(RuntimeError, match="ACL cleanup failed"):
        await service.delete_schedule(
            str(schedule.workflow_definition_id),
            str(schedule.id),
            str(schedule.created_by),
        )

    schedule.delete.assert_awaited_once_with(session=session)
    acl_service.delete_acl_entries_for_resource.assert_awaited_once_with(
        resource_type="workflowSchedule",
        resource_id=schedule.id,
        session=session,
    )
