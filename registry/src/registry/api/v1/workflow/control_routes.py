from __future__ import annotations

import logging

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Request

from registry.auth.dependencies import CurrentUser, UserContextDict
from registry.deps import get_acl_service, get_workflow_control_service
from registry.schemas.workflow_schemas import (
    DirectiveResponse,
    ResolveRequirementRequest,
    ResolveRequirementResponse,
    RetryRequest,
)
from registry.services.access_control_service import ACLService
from registry.services.workflow_control_service import WorkflowControlService
from registry_pkgs.models.extended_access_role import RegistryResourceType

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workflows/{workflow_id}/runs/{run_id}",
    tags=["Workflow Control"],
)


async def _require_workflow_view(
    acl_service: ACLService,
    user_context: UserContextDict,
    workflow_id: str,
) -> None:
    """Enforce VIEWER (VIEW) permission on the parent workflow for run directives.

    Raises HTTPException(403) if the caller lacks VIEW; HTTPException(404) if the
    workflow_id is malformed.
    """
    try:
        workflow_oid = PydanticObjectId(workflow_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found") from exc
    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_context.get("user_id")),
        resource_type=RegistryResourceType.WORKFLOW,
        resource_id=workflow_oid,
        required_permission="VIEW",
    )


@router.post("/pause", response_model=DirectiveResponse)
async def pause_run(
    workflow_id: str,
    run_id: str,
    current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
    acl_service: ACLService = Depends(get_acl_service),
) -> DirectiveResponse:
    """Pause a running workflow run.

    The pause takes effect at the next step boundary — any step currently
    in-flight will complete before the run enters the PAUSED state in MongoDB.
    Idempotent: pausing an already-paused run returns 200.
    """
    try:
        await _require_workflow_view(acl_service, current_user, workflow_id)
        run = await service.send_pause(workflow_id, run_id)
        return DirectiveResponse(run_id=str(run.id), status=run.status, message="Pause directive sent")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Pause request failed for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/resume", response_model=DirectiveResponse)
async def resume_run(
    workflow_id: str,
    run_id: str,
    current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
    acl_service: ACLService = Depends(get_acl_service),
) -> DirectiveResponse:
    """Resume a paused workflow run.

    Returns 400 if the run is not currently in PAUSED status.
    """
    try:
        await _require_workflow_view(acl_service, current_user, workflow_id)
        run = await service.send_resume(workflow_id, run_id)
        return DirectiveResponse(run_id=str(run.id), status=run.status, message="Resume directive sent")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Resume request failed for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/cancel", response_model=DirectiveResponse)
async def cancel_run(
    workflow_id: str,
    run_id: str,
    current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
    acl_service: ACLService = Depends(get_acl_service),
) -> DirectiveResponse:
    """Cancel a running or paused workflow run.

    Idempotent: cancelling an already-cancelled run returns 200.
    Returns 400 if the run has already reached a terminal state (completed or failed).
    """
    try:
        await _require_workflow_view(acl_service, current_user, workflow_id)
        run = await service.send_cancel(workflow_id, run_id)
        return DirectiveResponse(run_id=str(run.id), status=run.status, message="Cancel directive sent")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Cancel request failed for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/retry", response_model=DirectiveResponse)
async def retry_run(
    workflow_id: str,
    run_id: str,
    body: RetryRequest,
    request: Request,
    current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
    acl_service: ACLService = Depends(get_acl_service),
) -> DirectiveResponse:
    """Retry a finished workflow run from a specific node.

    Creates a child WorkflowRun that replays cached outputs for all nodes
    that completed successfully before *from_node_id*, then re-executes
    *from_node_id* and every subsequent node.

    Returns 400 if the run has not yet finished (still RUNNING or PAUSED).
    Returns 400 if *from_node_id* does not exist in the workflow definition.
    """
    auth_header = request.headers.get("Authorization", "")
    registry_token = auth_header.removeprefix("Bearer ").strip()
    user_id = current_user.get("user_id")

    try:
        await _require_workflow_view(acl_service, current_user, workflow_id)
        child_run = await service.send_retry(
            workflow_id,
            run_id,
            body.from_node_id,
            registry_token=registry_token,
            user_id=user_id,
        )
        return DirectiveResponse(
            run_id=str(child_run.id),
            status=child_run.status,
            message=f"Retry run created from node {body.from_node_id!r}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Retry request failed for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/approve", response_model=ResolveRequirementResponse)
async def approve_run(
    workflow_id: str,
    run_id: str,
    body: ResolveRequirementRequest,
    current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
    acl_service: ACLService = Depends(get_acl_service),
) -> ResolveRequirementResponse:
    """Resolve a pending requirement on a run holding at an HITL gate.

    Carries a rich decision: confirm / reject / edit / user_input /
    route_select — mirroring agno's ``StepRequirement`` methods 1:1.

    Status codes:
    - ``200``  decision accepted; ``continue_run`` triggered in the background
    - ``400``  resolution / requirement type mismatch (e.g. EDIT on a non-output-review
               requirement); missing required field (editedOutput / userInput / selectedChoices)
    - ``403``  caller lacks ``workflows-control`` scope or VIEW permission on the workflow
    - ``404``  workflow, run, or step_id not found (incl. already-resolved requirement)
    - ``409``  run not in ``awaiting_approval`` (already resolved by someone else, timed out, completed)
    """
    try:
        await _require_workflow_view(acl_service, current_user, workflow_id)
        run = await service.resolve_requirement(
            workflow_id,
            run_id,
            step_id=body.stepId,
            resolution=body.resolution,
            feedback=body.feedback,
            edited_output=body.editedOutput,
            user_input=body.userInput,
            selected_choices=body.selectedChoices,
        )
        return ResolveRequirementResponse(
            runId=str(run.id),
            status=run.status,
            resolvedStepId=body.stepId,
            message=f"Requirement resolved as {body.resolution.value}; run resuming",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Approve request failed for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc
