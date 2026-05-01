from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from registry.auth.dependencies import CurrentUser
from registry.deps import get_workflow_control_service
from registry.schemas.workflow_schemas import (
    DirectiveResponse,
    RetryRequest,
)
from registry.services.workflow_control_service import WorkflowControlService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workflows/{workflow_id}/runs/{run_id}",
    tags=["Workflow Control"],
)


@router.post("/pause", response_model=DirectiveResponse)
async def pause_run(
    workflow_id: str,
    run_id: str,
    _current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
) -> DirectiveResponse:
    """Pause a running workflow run.

    The pause takes effect at the next step boundary — any step currently
    in-flight will complete before the run enters the PAUSED state in MongoDB.
    Idempotent: pausing an already-paused run returns 200.
    """
    try:
        run = await service.send_pause(workflow_id, run_id)
        return DirectiveResponse(run_id=str(run.id), status=run.status, message="Run paused")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Pause request failed for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/resume", response_model=DirectiveResponse)
async def resume_run(
    workflow_id: str,
    run_id: str,
    _current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
) -> DirectiveResponse:
    """Resume a paused workflow run.

    Returns 400 if the run is not currently in PAUSED status.
    """
    try:
        run = await service.send_resume(workflow_id, run_id)
        return DirectiveResponse(run_id=str(run.id), status=run.status, message="Run resumed")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Resume request failed for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/cancel", response_model=DirectiveResponse)
async def cancel_run(
    workflow_id: str,
    run_id: str,
    _current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
) -> DirectiveResponse:
    """Cancel a running or paused workflow run.

    Idempotent: cancelling an already-cancelled run returns 200.
    Returns 400 if the run has already reached a terminal state (completed or failed).
    """
    try:
        run = await service.send_cancel(workflow_id, run_id)
        return DirectiveResponse(run_id=str(run.id), status=run.status, message="Run cancelled")
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
    _current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
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
    user_id = _current_user.get("user_id")

    try:
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
