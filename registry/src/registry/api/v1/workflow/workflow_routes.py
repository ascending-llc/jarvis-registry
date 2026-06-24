"""
Workflow Management API Routes V1

RESTful API endpoints for managing workflows and workflow runs using MongoDB.
"""

import logging
import math
from typing import Annotated, Literal

from beanie import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi import status as http_status

from registry.api.v1.workflow.token_helpers import build_registry_token
from registry.auth.dependencies import CurrentUser, UserContextDict
from registry.core.telemetry_decorators import track_registry_operation
from registry.deps import get_acl_service, get_workflow_control_service, get_workflow_runner, get_workflow_service
from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.errors import ErrorCode, create_error_detail
from registry.schemas.workflow_api_schemas import (
    NodeRunOutput,
    PaginationMetadata,
    StepRequirementSummary,
    WorkflowCreateRequest,
    WorkflowDetailResponse,
    WorkflowListResponse,
    WorkflowRunDetailResponse,
    WorkflowRunListResponse,
    WorkflowRunTriggerRequest,
    WorkflowRunTriggerResponse,
    WorkflowToggleRequest,
    WorkflowUpdateRequest,
    WorkflowVersionItem,
    WorkflowVersionListResponse,
    convert_node_run_to_output,
    convert_to_detail,
    convert_to_list_item,
    convert_to_run_detail,
    convert_to_run_list_item,
)
from registry.schemas.workflow_schemas import NodeRunListResponse, NodeRunSummary, RunStatusResponse
from registry.services.access_control_service import ACLService
from registry.services.workflow_control_service import WorkflowControlService
from registry.services.workflow_executor import execute_workflow_run_background
from registry.services.workflow_service import WorkflowService
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import PrincipalType
from registry_pkgs.models.enums import RoleBits
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.workflows.runner import WorkflowRunner

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authorize_workflow(
    acl_service: ACLService,
    workflow_service: WorkflowService,
    user_context: UserContextDict,
    workflow_id: str,
    required_permission: str,
):
    """Resolve a workflow (404 if missing) and enforce an ACL permission on it.

    Returns the (workflow, ResourcePermissions) tuple on success; raises
    ValueError (→404) if not found or HTTPException(403) if unauthorized.
    """
    workflow = await workflow_service.get_workflow_by_id(workflow_id)
    permissions = await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_context.get("user_id")),
        resource_type=RegistryResourceType.WORKFLOW,
        resource_id=workflow.id,
        required_permission=required_permission,
    )
    return workflow, permissions


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
    acl_service: ACLService = Depends(get_acl_service),
):
    """
    List workflows with optional filtering and pagination.

    Only workflows the caller has VIEW permission on are returned.

    Query Parameters:
    - query: Free-text search across workflow name, description
    - page: Page number (default: 1, min: 1)
    - per_page: Items per page (default: 20, min: 1, max: 100)
    """
    try:
        user_id = user_context.get("user_id")
        accessible_ids = await acl_service.get_accessible_resource_ids(
            user_id=PydanticObjectId(user_id),
            resource_type=RegistryResourceType.WORKFLOW.value,
        )

        # List workflows restricted to ACL-accessible IDs
        workflows, total = await workflow_service.list_workflows(
            query=query,
            page=page,
            per_page=per_page,
            accessible_workflow_ids=accessible_ids,
        )

        # Convert to response items with per-workflow permissions.
        perms_by_id = await acl_service.get_user_permissions_for_resources(
            user_id=PydanticObjectId(user_id),
            resource_type=RegistryResourceType.WORKFLOW.value,
            resource_ids=[w.id for w in workflows],
        )
        workflow_items = [convert_to_list_item(w, acl_permission=perms_by_id[w.id]) for w in workflows]

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
    acl_service: ACLService = Depends(get_acl_service),
):
    """Get detailed information about a workflow by ID (requires VIEW)"""
    try:
        workflow, permissions = await _authorize_workflow(
            acl_service, workflow_service, user_context, workflow_id, "VIEW"
        )

        # Convert to response model
        return convert_to_detail(workflow, acl_permission=permissions)

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
    acl_service: ACLService = Depends(get_acl_service),
):
    """Create a new workflow. The creator is granted OWNER permission."""
    try:
        user_id = user_context.get("user_id")

        # Create workflow
        workflow = await workflow_service.create_workflow(data=data)

        if not workflow:
            logger.error("Workflow creation failed without exception")
            raise ValueError("Failed to create workflow")

        # Grant OWNER permission to creator
        await acl_service.grant_permission(
            principal_type=PrincipalType.USER,
            principal_id=PydanticObjectId(user_id),
            resource_type=RegistryResourceType.WORKFLOW,
            resource_id=workflow.id,
            perm_bits=RoleBits.OWNER,
        )
        logger.info(f"Created workflow {workflow.id}: {workflow.name}; granted OWNER to user {user_id}")

        return convert_to_detail(
            workflow,
            acl_permission=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True),
        )

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
    acl_service: ACLService = Depends(get_acl_service),
):
    """Update a workflow with partial data (requires EDIT)"""
    try:
        _, permissions = await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "EDIT")

        # Update workflow (bumps version, snapshots prior version as history)
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                workflow = await workflow_service.update_workflow(
                    workflow_id=workflow_id,
                    data=data,
                    session=mongo_session,
                )

        return convert_to_detail(workflow, acl_permission=permissions)

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
    acl_service: ACLService = Depends(get_acl_service),
):
    """Delete a workflow (requires DELETE)"""
    try:
        workflow, _ = await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "DELETE")

        # Delete workflow
        successful_delete = await workflow_service.delete_workflow(workflow_id=workflow_id)

        if successful_delete:
            await acl_service.delete_acl_entries_for_resource(
                resource_type=RegistryResourceType.WORKFLOW.value,
                resource_id=workflow.id,
            )
            logger.info(f"Deleted workflow {workflow_id} and its ACL entries")
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
    acl_service: ACLService = Depends(get_acl_service),
):
    """Toggle workflow enabled/disabled status"""

    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_context.get("user_id")),
        resource_type=RegistryResourceType.WORKFLOW,
        resource_id=PydanticObjectId(workflow_id),
        required_permission="EDIT",
    )

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


@router.get(
    "/workflows/{workflow_id}/versions",
    response_model=WorkflowVersionListResponse,
    response_model_by_alias=True,
    summary="List Workflow Versions",
    description="List the version history of a workflow",
)
@track_registry_operation("list", resource_type="workflow_version")
async def list_workflow_versions(
    workflow_id: str,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """List the version history of a workflow (requires VIEW)."""
    try:
        await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "VIEW")

        versions = await workflow_service.list_versions(workflow_id)
        return WorkflowVersionListResponse(
            versions=[
                WorkflowVersionItem(
                    version=v["version"],
                    createdAt=v["created_at"],
                    checksum=v["checksum"],
                )
                for v in versions
            ]
        )

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
        logger.exception("HTTPException in list_workflow_versions")
        raise
    except Exception:
        logger.exception("Error listing versions for workflow %s", workflow_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while listing versions"),
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
    acl_service: ACLService = Depends(get_acl_service),
):
    """
    Trigger a workflow run (async execution).

    Requires VIEWER (VIEW) permission on the workflow plus the workflows-control scope.
    Returns 202 Accepted immediately. The workflow will be executed asynchronously in the background.
    """
    try:
        # Running a workflow requires VIEWER or more on the workflow itself
        await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "VIEW")

        # Create workflow run record (status=PENDING)
        run = await workflow_service.trigger_workflow_run(
            workflow_id=workflow_id,
            trigger_source=data.triggerSource,
            initial_input=data.initialInput,
            parent_run_id=data.parentRunId,
            resolved_dependencies=[dep.model_dump(by_alias=True) for dep in data.resolvedDependencies],
            version=data.version,
            triggering_user_id=user_context.get("user_id"),
            triggering_username=user_context.get("username"),
            triggering_scopes=user_context.get("scopes", []),
        )

        registry_token = build_registry_token(request, user_context)
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
        Literal["pending", "running", "paused", "awaiting_approval", "completed", "failed", "cancelled"] | None,
        Query(),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """
    List workflow runs with optional filtering and pagination (requires VIEW on the workflow).

    Query Parameters:
    - status: Filter by run status (pending, running, paused, awaiting_approval, completed, failed, cancelled)
    - page: Page number (default: 1, min: 1)
    - per_page: Items per page (default: 20, min: 1, max: 100)
    """
    try:
        await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "VIEW")

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
    "/workflows/{workflow_id}/runs/{run_id}/children",
    response_model=WorkflowRunListResponse,
    response_model_by_alias=True,
    summary="List Child Runs",
    description="List runs spawned from a parent run via node rerun, replay, or retry (by parent_run_id)",
)
@track_registry_operation("list", resource_type="workflow_run")
async def list_child_runs(
    workflow_id: str,
    run_id: str,
    user_context: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """List child runs of a parent run (requires VIEW on the workflow).

    Child runs are created by node rerun (``trigger_source="node_rerun"``),
    replay (``"replay"``), or retry (``"retry"``); each carries
    ``parentRunId == run_id``. Ordered newest-first.

    Query Parameters:
    - page: Page number (default: 1, min: 1)
    - per_page: Items per page (default: 20, min: 1, max: 100)
    """
    try:
        await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "VIEW")

        runs_with_nodes, total = await workflow_service.list_child_runs(
            workflow_id=workflow_id,
            parent_run_id=run_id,
            page=page,
            per_page=per_page,
        )

        run_items = [convert_to_run_list_item(run, node_runs) for run, node_runs in runs_with_nodes]
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
        logger.exception("HTTPException in list_child_runs")
        raise
    except Exception:
        logger.exception("Error listing child runs for parent run %s", run_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while listing child runs"),
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
    acl_service: ACLService = Depends(get_acl_service),
):
    """Get detailed information about a workflow run by ID (requires VIEW on the workflow)"""
    try:
        await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "VIEW")

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


@router.get(
    "/workflows/{workflow_id}/runs/{run_id}/status",
    response_model=RunStatusResponse,
    response_model_by_alias=True,
    summary="Get Workflow Run Status",
    description="Get run status with per-node summary. If the run is awaiting_approval and a pending requirement has timed out, a background continue_run is nudged so agno can apply the gate's on_timeout policy.",
)
@track_registry_operation("read", resource_type="workflow_run")
async def get_workflow_run_status(
    workflow_id: str,
    run_id: str,
    user_context: CurrentUser,
    workflow_control_service: WorkflowControlService = Depends(get_workflow_control_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """Pollable status endpoint for a workflow run (requires VIEW on the workflow).

    Side effect: when the run is ``awaiting_approval`` and a pending requirement has
    passed its ``timeout_at``, this lazily triggers ``continue_run`` so agno can
    apply the gate's ``on_timeout`` policy. The returned status still shows
    ``awaiting_approval`` — the actual state transition surfaces on the next poll.
    """
    try:
        workflow_oid = PydanticObjectId(workflow_id)
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, f"Workflow {workflow_id!r} not found"),
        ) from exc

    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_context.get("user_id")),
        resource_type=RegistryResourceType.WORKFLOW,
        resource_id=workflow_oid,
        required_permission="VIEW",
    )

    try:
        run, node_runs = await workflow_control_service.get_run_status(workflow_id, run_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, str(exc)),
        ) from exc
    except Exception as exc:
        logger.exception("Error getting workflow run status %s", run_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(
                ErrorCode.INTERNAL_ERROR, "Internal server error while getting workflow run status"
            ),
        ) from exc

    return RunStatusResponse(
        run_id=str(run.id),
        workflow_id=str(run.workflow_definition_id),
        status=run.status.value if hasattr(run.status, "value") else run.status,
        trigger_source=run.trigger_source,
        started_at=run.started_at,
        finished_at=run.finished_at,
        paused_at=run.paused_at,
        error_summary=run.error_summary,
        parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
        node_runs=[
            NodeRunSummary(
                node_id=nr.node_id,
                node_name=nr.node_name,
                status=nr.status.value if hasattr(nr.status, "value") else nr.status,
                attempt=nr.attempt,
                started_at=nr.started_at,
                finished_at=nr.finished_at,
                error=nr.error,
            )
            for nr in node_runs
        ],
        pendingRequirements=[StepRequirementSummary.model_validate(req) for req in (run.pending_requirements or [])],
    )


@router.get(
    "/workflows/{workflow_id}/runs/{run_id}/nodes",
    response_model=NodeRunListResponse,
    summary="List Node Runs",
    description="List all node runs for a workflow run with full I/O snapshots",
)
@track_registry_operation("list", resource_type="node_run")
async def list_node_runs(
    workflow_id: str,
    run_id: str,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """Return all NodeRuns for a WorkflowRun, including input/output snapshots (requires VIEW)."""
    try:
        await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "VIEW")
        _, node_runs = await workflow_service.get_workflow_run(workflow_id=workflow_id, run_id=run_id)
        return NodeRunListResponse(
            runId=run_id,
            workflowId=workflow_id,
            nodeRuns=[convert_node_run_to_output(nr) for nr in node_runs],
        )
    except ValueError as exc:
        err = str(exc)
        if "not found" in err.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, err),
            )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, err),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error listing node runs for run %s", run_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        )


@router.get(
    "/workflows/{workflow_id}/runs/{run_id}/nodes/{node_run_id}",
    response_model=NodeRunOutput,
    summary="Get Node Run Detail",
    description="Get full detail of a single node run including I/O snapshots",
)
@track_registry_operation("read", resource_type="node_run")
async def get_node_run(
    workflow_id: str,
    run_id: str,
    node_run_id: str,
    user_context: CurrentUser,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """Return a single NodeRun by ID (requires VIEW on the workflow)."""
    try:
        await _authorize_workflow(acl_service, workflow_service, user_context, workflow_id, "VIEW")
        run = await workflow_service.get_workflow_run_doc(workflow_id=workflow_id, run_id=run_id)
        nr = await workflow_service.get_node_run(str(run.id), node_run_id)
        return convert_node_run_to_output(nr)
    except ValueError as exc:
        err = str(exc)
        if "not found" in err.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, err),
            )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, err),
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error getting node run %s", node_run_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
        )
