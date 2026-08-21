from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.api.v1.workflow.schedule_routes import (
    create_schedule,
    delete_schedule,
    get_schedule,
    list_schedules,
    toggle_schedule,
    update_schedule,
)
from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.workflow_schedule_schemas import (
    ScheduleCreateRequest,
    ScheduleToggleRequest,
    ScheduleUpdateRequest,
)
from registry.services.workflow_schedule_service import ScheduleAccess
from registry_pkgs.models.enums import WorkflowRunStatus


@pytest.fixture
def user_context() -> dict:
    return {
        "user_id": str(PydanticObjectId()),
        "client_id": "registry-client",
        "username": "test-user",
        "groups": [],
        "scopes": ["registry-admin"],
        "auth_method": "jwt",
        "provider": "jwt",
        "auth_source": "jwt_auth",
    }


@pytest.fixture
def schedule() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        cron_expression="0 2 * * *",
        timezone="UTC",
        initial_input={"message": "hello"},
        enabled=True,
        next_run_at=now,
        last_run_at=now,
        last_run_id=PydanticObjectId(),
        last_run_status=WorkflowRunStatus.COMPLETED,
        created_by=PydanticObjectId(),
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def service(schedule: SimpleNamespace) -> MagicMock:
    mock = MagicMock()
    access = ScheduleAccess(
        schedule=schedule,
        permissions=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True),
    )
    mock.create_schedule = AsyncMock(return_value=access)
    mock.list_schedules = AsyncMock(return_value=[access])
    mock.get_schedule = AsyncMock(return_value=access)
    mock.update_schedule = AsyncMock(return_value=access)
    mock.delete_schedule = AsyncMock()
    mock.toggle_schedule = AsyncMock(return_value=access)
    return mock


@pytest.mark.asyncio
async def test_schedule_routes_track_operations_and_convert_responses(
    user_context: dict,
    schedule: SimpleNamespace,
    service: MagicMock,
) -> None:
    workflow_id = str(schedule.workflow_definition_id)
    schedule_id = str(schedule.id)

    with patch("registry.core.telemetry_decorators._record_registry_operation") as record_operation:
        created = await create_schedule(
            workflow_id=workflow_id,
            data=ScheduleCreateRequest(cron_expression="0 2 * * *"),
            user_context=user_context,
            service=service,
        )
        listed = await list_schedules(workflow_id=workflow_id, user_context=user_context, service=service)
        fetched = await get_schedule(
            workflow_id=workflow_id,
            schedule_id=schedule_id,
            user_context=user_context,
            service=service,
        )
        updated = await update_schedule(
            workflow_id=workflow_id,
            schedule_id=schedule_id,
            data=ScheduleUpdateRequest(timezone="Asia/Shanghai"),
            user_context=user_context,
            service=service,
        )
        deleted = await delete_schedule(
            workflow_id=workflow_id,
            schedule_id=schedule_id,
            user_context=user_context,
            service=service,
        )
        toggled = await toggle_schedule(
            workflow_id=workflow_id,
            schedule_id=schedule_id,
            data=ScheduleToggleRequest(enabled=True),
            user_context=user_context,
            service=service,
        )

    assert created.id == schedule_id
    assert created.last_run_id == str(schedule.last_run_id)
    assert created.last_run_status == WorkflowRunStatus.COMPLETED.value
    assert created.permissions.DELETE is True
    assert listed.total == 1
    assert listed.items == [created]
    assert fetched == created
    assert updated == created
    assert deleted.status_code == 204
    assert toggled == created

    assert [call.kwargs["operation"] for call in record_operation.call_args_list] == [
        "create",
        "list",
        "read",
        "update",
        "delete",
        "update",
    ]
    assert {call.kwargs["resource_type"] for call in record_operation.call_args_list} == {"workflowSchedule"}
    assert all(call.kwargs["success"] is True for call in record_operation.call_args_list)


@pytest.mark.asyncio
async def test_create_schedule_returns_structured_403_without_user_id(service: MagicMock) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await create_schedule(
            workflow_id=str(PydanticObjectId()),
            data=ScheduleCreateRequest(cron_expression="0 2 * * *"),
            user_context={},
            service=service,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "error": "insufficient_permissions",
        "message": "User identity required for workflow operations",
    }
    service.create_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_schedule_preserves_service_http_exception(user_context: dict, service: MagicMock) -> None:
    service.get_schedule.side_effect = HTTPException(status_code=404, detail="Schedule not found")

    with pytest.raises(HTTPException) as exc_info:
        await get_schedule(
            workflow_id=str(PydanticObjectId()),
            schedule_id=str(PydanticObjectId()),
            user_context=user_context,
            service=service,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Schedule not found"


@pytest.mark.asyncio
async def test_get_schedule_wraps_unexpected_exception(user_context: dict, service: MagicMock) -> None:
    service.get_schedule.side_effect = ValueError("unexpected")

    with pytest.raises(HTTPException) as exc_info:
        await get_schedule(
            workflow_id=str(PydanticObjectId()),
            schedule_id=str(PydanticObjectId()),
            user_context=user_context,
            service=service,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {"error": "internal_error", "message": "Internal server error"}


@pytest.mark.asyncio
async def test_list_schedules_maps_runtime_error_to_service_unavailable(
    user_context: dict,
    service: MagicMock,
) -> None:
    service.list_schedules.side_effect = RuntimeError("ACL unavailable")

    with pytest.raises(HTTPException) as exc_info:
        await list_schedules(
            workflow_id=str(PydanticObjectId()),
            user_context=user_context,
            service=service,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error": "service_unavailable",
        "message": "Workflow schedule listing is temporarily unavailable. Please try again later.",
    }
