"""Workflow schedule CRUD and enable/disable routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from registry.auth.dependencies import CurrentUser
from registry.deps import get_workflow_schedule_service
from registry.schemas.workflow_schedule_schemas import (
    ScheduleCreateRequest,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleToggleRequest,
    ScheduleUpdateRequest,
)
from registry.services.workflow_schedule_service import WorkflowScheduleService
from registry_pkgs.models import WorkflowSchedule

logger = logging.getLogger(__name__)

router = APIRouter()


def _user_id(user_context: dict) -> str:
    user_id = user_context.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User identity is required")
    return user_id


def _response(schedule: WorkflowSchedule) -> ScheduleResponse:
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
    )


@router.post(
    "/workflows/{workflow_id}/schedules",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    workflow_id: str,
    data: ScheduleCreateRequest,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        return _response(await service.create_schedule(workflow_id, data, _user_id(user_context)))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create schedule for workflow %s", workflow_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/workflows/{workflow_id}/schedules", response_model=ScheduleListResponse)
async def list_schedules(
    workflow_id: str,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleListResponse:
    try:
        schedules = await service.list_schedules(workflow_id, _user_id(user_context))
        return ScheduleListResponse(items=[_response(schedule) for schedule in schedules], total=len(schedules))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list schedules for workflow %s", workflow_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get("/workflows/{workflow_id}/schedules/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    workflow_id: str,
    schedule_id: str,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        return _response(await service.get_schedule(workflow_id, schedule_id, _user_id(user_context)))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get schedule %s", schedule_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.put("/workflows/{workflow_id}/schedules/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    workflow_id: str,
    schedule_id: str,
    data: ScheduleUpdateRequest,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        return _response(await service.update_schedule(workflow_id, schedule_id, data, _user_id(user_context)))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to update schedule %s", schedule_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.delete(
    "/workflows/{workflow_id}/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_schedule(
    workflow_id: str,
    schedule_id: str,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> Response:
    try:
        await service.delete_schedule(workflow_id, schedule_id, _user_id(user_context))
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete schedule %s", schedule_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/workflows/{workflow_id}/schedules/{schedule_id}/toggle", response_model=ScheduleResponse)
async def toggle_schedule(
    workflow_id: str,
    schedule_id: str,
    data: ScheduleToggleRequest,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        return _response(await service.toggle_schedule(workflow_id, schedule_id, data.enabled, _user_id(user_context)))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to toggle schedule %s", schedule_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc
