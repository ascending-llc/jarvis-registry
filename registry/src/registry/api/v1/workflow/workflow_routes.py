"""
Workflow Management API Routes V1

RESTful API endpoints for managing workflows and workflow runs using MongoDB.
"""

import logging
import math
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi import status as http_status

from registry.auth.dependencies import CurrentUser, UserContextDict
from registry.core.telemetry_decorators import track_registry_operation
from registry.deps import get_workflow_runner, get_workflow_service
from registry.schemas.errors import ErrorCode, create_error_detail
from registry.schemas.workflow_api_schemas import (
    PaginationMetadata,
    WorkflowCreateRequest,
    WorkflowDetailResponse,
    WorkflowListResponse,
    WorkflowRunDetailResponse,
    WorkflowRunListResponse,
    WorkflowRunTriggerRequest,
    WorkflowRunTriggerResponse,
    WorkflowToggleRequest,
    WorkflowUpdateRequest,
    convert_to_detail,
    convert_to_list_item,
    convert_to_run_detail,
    convert_to_run_list_item,
)
from registry.services.workflow_executor import execute_workflow_run_background
from registry.services.workflow_service import WorkflowService
from registry.utils.crypto_utils import generate_service_jwt
from registry_pkgs.workflows.runner import WorkflowRunner

logger = logging.getLogger(__name__)

router = APIRouter()


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.removeprefix("Bearer ").strip()


def _build_registry_token(
    request: Request,
    user_context: UserContextDict,
) -> str:
    header_token = _extract_bearer_token(request)
    if header_token:
        logger.debug("Extracted registry token from Authorization header (length: %s)", len(header_token))
        return header_token

    user_id = user_context.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail=create_error_detail(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "Authenticated user context is missing user_id",
            ),
        )

    token = generate_service_jwt(
        user_id=user_id,
        username=user_context.get("username"),
        scopes=user_context.get("scopes", []),
    )
    logger.debug("Generated service JWT for workflow execution (length: %s)", len(token))
    return token


# ==================== Workflow Endpoints ====================


@router.get(
    "/workflows",
    response_model=WorkflowListResponse,
    response_model_by_alias=True,
    summary="List Workflows",
    description="List all workflows with filtering, searching, and pagination",
)
@track_registry_operation("list", resource_type="workflow")
async def list_workflows(
    user_context: CurrentUser,
    query: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """
    List workflows with optional filtering and pagination.

    Query Parameters:
    - query: Free-text search across workflow name, description
    - page: Page number (default: 1, min: 1)
    - per_page: Items per page (default: 20, min: 1, max: 100)
    """
    try:
        # List workflows
        workflows, total = await workflow_service.list_workflows(
            query=query,
            page=page,
            per_page=per_page,
        )

        # Convert to response items
        workflow_items = [convert_to_list_item(workflow) for workflow in workflows]

        # Calculate pagination metadata
        total_pages = math.ceil(total / per_page) if total > 0 else 0

        return WorkflowListResponse(
            workflows=workflow_items,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                perPage=per_page,
                totalPages=total_pages,
            ),
        )

    except HTTPException:
        logger.exception("HTTPException in list_workflows")
        raise
    except Exception:
        logger.exception("Error listing workflows")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while listing workflows"),
        )


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowDetailResponse,
    response_model_by_alias=True,
    summary="Get Workflow Detail",
    description="Get detailed information about a specific workflow",
)
@track_registry_operation("read", resource_type="workflow")
async def get_workflow(
    workflow_id: str,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """Get detailed information about a workflow by ID"""
    try:
        # Get workflow
        workflow = await workflow_service.get_workflow_by_id(workflow_id)

        # Convert to response model
        return convert_to_detail(workflow)

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )
    except HTTPException:
        logger.exception("HTTPException in get_workflow")
        raise
    except Exception:
        logger.exception("Error getting workflow %s", workflow_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while getting workflow"),
        )


@router.post(
    "/workflows",
    response_model=WorkflowDetailResponse,
    response_model_by_alias=True,
    status_code=http_status.HTTP_201_CREATED,
    summary="Create Workflow",
    description="Create a new workflow",
)
@track_registry_operation("create", resource_type="workflow")
async def create_workflow(
    data: WorkflowCreateRequest,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """Create a new workflow"""
    try:
        # Create workflow
        workflow = await workflow_service.create_workflow(data=data)

        if not workflow:
            logger.error("Workflow creation failed without exception")
            raise ValueError("Failed to create workflow")

        logger.info(f"Created workflow {workflow.id}: {workflow.name}")

        return convert_to_detail(workflow)

    except ValueError as e:
        error_msg = str(e)

        # Validation errors
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in create_workflow")
        raise
    except Exception:
        logger.exception("Error creating workflow")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while creating workflow"),
        )


@router.put(
    "/workflows/{workflow_id}",
    response_model=WorkflowDetailResponse,
    response_model_by_alias=True,
    summary="Update Workflow",
    description="Update workflow configuration",
)
@track_registry_operation("update", resource_type="workflow")
async def update_workflow(
    workflow_id: str,
    data: WorkflowUpdateRequest,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """Update a workflow with partial data"""
    try:
        # Update workflow
        workflow = await workflow_service.update_workflow(workflow_id=workflow_id, data=data)

        return convert_to_detail(workflow)

    except ValueError as e:
        error_msg = str(e)

        # Check if workflow not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )

        # Other validation errors
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in update_workflow")
        raise
    except Exception:
        logger.exception("Error updating workflow %s", workflow_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while updating workflow"),
        )


@router.delete(
    "/workflows/{workflow_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    summary="Delete Workflow",
    description="Delete a workflow",
)
@track_registry_operation("delete", resource_type="workflow")
async def delete_workflow(
    workflow_id: str,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """Delete a workflow"""
    try:
        # Delete workflow
        successful_delete = await workflow_service.delete_workflow(workflow_id=workflow_id)

        if successful_delete:
            logger.info(f"Deleted workflow {workflow_id}")
            return None  # 204 No Content
        else:
            raise ValueError(f"Failed to delete workflow {workflow_id}")

    except ValueError as e:
        error_msg = str(e)

        # Not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, "Workflow not found"),
            )

        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in delete_workflow")
        raise
    except Exception:
        logger.exception("Error deleting workflow %s", workflow_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while deleting workflow"),
        )


@router.post(
    "/workflows/{workflow_id}/toggle",
    response_model=WorkflowDetailResponse,
    response_model_by_alias=True,
    summary="Toggle Workflow Status",
    description="Enable or disable a workflow",
)
@track_registry_operation("update", resource_type="workflow")
async def toggle_workflow(
    workflow_id: str,
    data: WorkflowToggleRequest,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """Toggle workflow enabled/disabled status"""
    try:
        # Toggle workflow status
        workflow = await workflow_service.toggle_workflow_status(
            workflow_id=workflow_id,
            enabled=data.enabled,
        )

        return convert_to_detail(workflow)

    except ValueError as e:
        error_msg = str(e)

        # Check if workflow not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )

        # Other validation errors
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in toggle_workflow")
        raise
    except Exception:
        logger.exception("Error toggling workflow %s", workflow_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while toggling workflow"),
        )


# ==================== Workflow Run Endpoints ====================


@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunTriggerResponse,
    response_model_by_alias=True,
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Trigger Workflow Run",
    description="Trigger a workflow run (async execution)",
)
@track_registry_operation("create", resource_type="workflow_run")
async def trigger_workflow_run(
    workflow_id: str,
    data: WorkflowRunTriggerRequest,
    background_tasks: BackgroundTasks,
    user_context: CurrentUser,
    request: Request,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    workflow_runner: WorkflowRunner = Depends(get_workflow_runner),
):
    """
    Trigger a workflow run (async execution).

    Returns 202 Accepted immediately. The workflow will be executed asynchronously in the background.
    """
    try:
        # Create workflow run record (status=PENDING)
        run = await workflow_service.trigger_workflow_run(
            workflow_id=workflow_id,
            trigger_source=data.triggerSource,
            initial_input=data.initialInput,
            parent_run_id=data.parentRunId,
            resolved_dependencies=[dep.model_dump(by_alias=True) for dep in data.resolvedDependencies],
        )

        registry_token = _build_registry_token(request, user_context)
        user_id = user_context.get("user_id")

        # Schedule background execution
        # This updates the run status as it progresses through the workflow state machine.
        background_tasks.add_task(
            execute_workflow_run_background,
            run_id=run.id,
            workflow_runner=workflow_runner,
            registry_token=registry_token,
            user_id=user_id,
        )

        logger.info(f"Workflow run {run.id} queued for execution (workflow: {workflow_id})")

        return WorkflowRunTriggerResponse(
            runId=str(run.id),
            workflowDefinitionId=str(run.workflow_definition_id),
            status=run.status.value if hasattr(run.status, "value") else run.status,
            triggerSource=run.trigger_source,
            startedAt=run.started_at,
            message="Workflow run queued for execution",
        )

    except ValueError as e:
        error_msg = str(e)

        # Check if workflow not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )

        # Other validation errors
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in trigger_workflow_run")
        raise
    except Exception:
        logger.exception("Error triggering workflow run for %s", workflow_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while triggering workflow run"),
        )


@router.get(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunListResponse,
    response_model_by_alias=True,
    summary="List Workflow Runs",
    description="List all runs for a specific workflow with filtering and pagination",
)
@track_registry_operation("list", resource_type="workflow_run")
async def list_workflow_runs(
    workflow_id: str,
    user_context: CurrentUser,
    status: Annotated[
        Literal["pending", "running", "paused", "completed", "failed", "cancelled"] | None,
        Query(),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """
    List workflow runs with optional filtering and pagination.

    Query Parameters:
    - status: Filter by run status (pending, running, paused, completed, failed, cancelled)
    - page: Page number (default: 1, min: 1)
    - per_page: Items per page (default: 20, min: 1, max: 100)
    """
    try:
        # List workflow runs
        runs_with_nodes, total = await workflow_service.list_workflow_runs(
            workflow_id=workflow_id,
            status=status,
            page=page,
            per_page=per_page,
        )

        # Convert to response items
        run_items = [convert_to_run_list_item(run, node_runs) for run, node_runs in runs_with_nodes]

        # Calculate pagination metadata
        total_pages = math.ceil(total / per_page) if total > 0 else 0

        return WorkflowRunListResponse(
            runs=run_items,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                perPage=per_page,
                totalPages=total_pages,
            ),
        )

    except ValueError as e:
        error_msg = str(e)

        # Check if workflow not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )

        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in list_workflow_runs")
        raise
    except Exception:
        logger.exception("Error listing workflow runs for %s", workflow_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while listing workflow runs"),
        )


@router.get(
    "/workflows/{workflow_id}/runs/{run_id}",
    response_model=WorkflowRunDetailResponse,
    response_model_by_alias=True,
    summary="Get Workflow Run Detail",
    description="Get detailed information about a specific workflow run",
)
@track_registry_operation("read", resource_type="workflow_run")
async def get_workflow_run(
    workflow_id: str,
    run_id: str,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    """Get detailed information about a workflow run by ID"""
    try:
        # Get workflow run with node runs
        run, node_runs = await workflow_service.get_workflow_run(workflow_id=workflow_id, run_id=run_id)

        # Convert to response model
        return convert_to_run_detail(run, node_runs)

    except ValueError as e:
        error_msg = str(e)

        # Check if workflow or run not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )

        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in get_workflow_run")
        raise
    except Exception:
        logger.exception("Error getting workflow run %s", run_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while getting workflow run"),
        )
