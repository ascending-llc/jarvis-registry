"""Request and response schemas for workflow schedules."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class ScheduleListResponse(BaseModel):
    items: list[ScheduleResponse]
    total: int
