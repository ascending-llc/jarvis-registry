"""Workflow schedule CRUD and enable/disable routes."""

import logging
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import status as http_status

from registry.auth.dependencies import CurrentUser, UserContextDict
from registry.core.telemetry_decorators import track_registry_operation
from registry.deps import get_workflow_schedule_service
from registry.schemas.errors import ErrorCode, create_error_detail
from registry.schemas.workflow_schedule_schemas import (
    ScheduleCreateRequest,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleToggleRequest,
    ScheduleUpdateRequest,
    convert_to_schedule_response,
)
from registry.services.workflow_schedule_service import WorkflowScheduleService
from registry_pkgs.models.extended_access_role import RegistryResourceType

logger = logging.getLogger(__name__)

router = APIRouter()

_WORKFLOW_SCHEDULE_RESOURCE = RegistryResourceType.WORKFLOW_SCHEDULE.value


def _require_user_id(user_context: UserContextDict) -> str:
    user_id = user_context.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=create_error_detail(
                ErrorCode.INSUFFICIENT_PERMISSIONS,
                "User identity required for workflow operations",
            ),
        )
    return user_id


def _raise_internal_error(exc: Exception) -> NoReturn:
    raise HTTPException(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
    ) from exc


def _raise_list_unavailable(exc: RuntimeError) -> NoReturn:
    raise HTTPException(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=create_error_detail(
            ErrorCode.SERVICE_UNAVAILABLE,
            "Workflow schedule listing is temporarily unavailable. Please try again later.",
        ),
    ) from exc


@router.post(
    "/workflows/{workflow_id}/schedules",
    response_model=ScheduleResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Create Workflow Schedule",
    description="Create a disabled recurring schedule for an enabled workflow.",
)
@track_registry_operation("create", resource_type=_WORKFLOW_SCHEDULE_RESOURCE)
async def create_schedule(
    workflow_id: str,
    data: ScheduleCreateRequest,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        access = await service.create_schedule(workflow_id, data, _require_user_id(user_context))
        return convert_to_schedule_response(access.schedule, access.permissions)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create schedule for workflow %s", workflow_id)
        _raise_internal_error(exc)


@router.get(
    "/workflows/{workflow_id}/schedules",
    response_model=ScheduleListResponse,
    summary="List Workflow Schedules",
    description="List recurring schedules for a workflow visible to the caller.",
)
@track_registry_operation("list", resource_type=_WORKFLOW_SCHEDULE_RESOURCE)
async def list_schedules(
    workflow_id: str,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleListResponse:
    try:
        schedules = await service.list_schedules(workflow_id, _require_user_id(user_context))
        return ScheduleListResponse(
            items=[convert_to_schedule_response(access.schedule, access.permissions) for access in schedules],
            total=len(schedules),
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        logger.exception("ACL lookup failed while listing schedules for workflow %s", workflow_id)
        _raise_list_unavailable(exc)
    except Exception as exc:
        logger.exception("Failed to list schedules for workflow %s", workflow_id)
        _raise_internal_error(exc)


@router.get(
    "/workflows/{workflow_id}/schedules/{schedule_id}",
    response_model=ScheduleResponse,
    summary="Get Workflow Schedule",
    description="Get one recurring workflow schedule.",
)
@track_registry_operation("read", resource_type=_WORKFLOW_SCHEDULE_RESOURCE)
async def get_schedule(
    workflow_id: str,
    schedule_id: str,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        access = await service.get_schedule(workflow_id, schedule_id, _require_user_id(user_context))
        return convert_to_schedule_response(access.schedule, access.permissions)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get schedule %s", schedule_id)
        _raise_internal_error(exc)


@router.put(
    "/workflows/{workflow_id}/schedules/{schedule_id}",
    response_model=ScheduleResponse,
    summary="Update Workflow Schedule",
    description="Update a schedule's CRON expression, timezone, or initial input.",
)
@track_registry_operation("update", resource_type=_WORKFLOW_SCHEDULE_RESOURCE)
async def update_schedule(
    workflow_id: str,
    schedule_id: str,
    data: ScheduleUpdateRequest,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        access = await service.update_schedule(
            workflow_id,
            schedule_id,
            data,
            _require_user_id(user_context),
        )
        return convert_to_schedule_response(access.schedule, access.permissions)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to update schedule %s", schedule_id)
        _raise_internal_error(exc)


@router.delete(
    "/workflows/{workflow_id}/schedules/{schedule_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    summary="Delete Workflow Schedule",
    description="Delete a recurring workflow schedule and its ACL entries.",
)
@track_registry_operation("delete", resource_type=_WORKFLOW_SCHEDULE_RESOURCE)
async def delete_schedule(
    workflow_id: str,
    schedule_id: str,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> Response:
    try:
        await service.delete_schedule(workflow_id, schedule_id, _require_user_id(user_context))
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete schedule %s", schedule_id)
        _raise_internal_error(exc)


@router.post(
    "/workflows/{workflow_id}/schedules/{schedule_id}/toggle",
    response_model=ScheduleResponse,
    summary="Enable or Disable Workflow Schedule",
    description="Enable a schedule and calculate its next run, or disable it and clear its lease.",
)
@track_registry_operation("update", resource_type=_WORKFLOW_SCHEDULE_RESOURCE)
async def toggle_schedule(
    workflow_id: str,
    schedule_id: str,
    data: ScheduleToggleRequest,
    user_context: CurrentUser,
    service: WorkflowScheduleService = Depends(get_workflow_schedule_service),
) -> ScheduleResponse:
    try:
        access = await service.toggle_schedule(
            workflow_id,
            schedule_id,
            data.enabled,
            _require_user_id(user_context),
        )
        return convert_to_schedule_response(access.schedule, access.permissions)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to toggle schedule %s", schedule_id)
        _raise_internal_error(exc)
