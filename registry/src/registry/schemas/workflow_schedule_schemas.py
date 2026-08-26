"""Request and response schemas for workflow schedules."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from registry_pkgs.models import WorkflowSchedule

from .acl_schema import ResourcePermissions


class ScheduleCreateRequest(BaseModel):
    cron_expression: str = Field(min_length=9, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    initial_input: dict[str, Any] | None = None


class ScheduleUpdateRequest(BaseModel):
    cron_expression: str | None = Field(default=None, min_length=9, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    initial_input: dict[str, Any] | None = None


class ScheduleToggleRequest(BaseModel):
    enabled: bool


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_definition_id: str
    cron_expression: str
    timezone: str
    initial_input: dict[str, Any] | None
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_id: str | None
    last_run_status: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    permissions: ResourcePermissions


class ScheduleListResponse(BaseModel):
    items: list[ScheduleResponse]
    total: int


def convert_to_schedule_response(
    schedule: WorkflowSchedule,
    permissions: ResourcePermissions,
) -> ScheduleResponse:
    """Convert a persisted workflow schedule into its public API representation."""
    return ScheduleResponse(
        id=str(schedule.id),
        workflow_definition_id=str(schedule.workflow_definition_id),
        cron_expression=schedule.cron_expression,
        timezone=schedule.timezone,
        initial_input=schedule.initial_input,
        enabled=schedule.enabled,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_run_id=str(schedule.last_run_id) if schedule.last_run_id else None,
        last_run_status=schedule.last_run_status.value if schedule.last_run_status else None,
        created_by=str(schedule.created_by),
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
        permissions=permissions,
    )
