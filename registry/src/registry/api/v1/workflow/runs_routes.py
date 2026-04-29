from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from registry.auth.dependencies import CurrentUser
from registry.deps import get_workflow_control_service
from registry.schemas.workflow_schemas import (
    NodeRunSummary,
    RunStatusResponse,
    RunSummary,
    TriggerRunRequest,
    TriggerRunResponse,
    WorkflowRunsStatusResponse,
)
from registry.services.workflow_control_service import WorkflowControlService

logger = logging.getLogger(__name__)


collection_router = APIRouter(
    prefix="/workflows/{workflow_id}/runs",
    tags=["Workflow Control"],
)


@collection_router.post("", response_model=TriggerRunResponse, status_code=202)
async def trigger_run(
    workflow_id: str,
    body: TriggerRunRequest,
    request: Request,
    _current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
) -> TriggerRunResponse:
    """Trigger a new workflow run for the given WorkflowDefinition.

    The run is created immediately with status ``pending`` and the runner starts
    in the background.  Returns 202 Accepted with the ``run_id`` so callers can
    poll ``GET /workflows/{workflow_id}/runs/{run_id}/status`` for progress.

    Returns 404 if the WorkflowDefinition does not exist.
    Returns 501 if the workflow runner is not configured on this instance.
    """
    auth_header = request.headers.get("Authorization", "")
    registry_token = auth_header.removeprefix("Bearer ").strip()
    user_id = _current_user.get("user_id")

    try:
        run = await service.trigger_run(
            workflow_id,
            body.user_text,
            registry_token=registry_token,
            user_id=user_id,
        )
        return TriggerRunResponse(
            run_id=str(run.id),
            workflow_id=str(run.workflow_definition_id),
            status=str(run.status),
        )
    except HTTPException as exc:
        logger.error("Error triggering run for workflow %s: %s", workflow_id, exc)
        raise
    except Exception as exc:
        logger.error("Unexpected error triggering run for workflow %s", workflow_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@collection_router.get("/status", response_model=WorkflowRunsStatusResponse)
async def list_runs_status(
    workflow_id: str,
    _current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
) -> WorkflowRunsStatusResponse:
    """List the status of all WorkflowRuns for a workflow definition.

    Returns runs ordered by ``started_at`` descending (newest first).
    Returns an empty list when the workflow has no runs yet.
    """
    try:
        runs = await service.get_workflow_runs(workflow_id)
        return WorkflowRunsStatusResponse(
            workflow_id=workflow_id,
            total=len(runs),
            runs=[
                RunSummary(
                    run_id=str(r.id),
                    status=str(r.status),
                    trigger_source=r.trigger_source,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                    error_summary=r.error_summary,
                    parent_run_id=str(r.parent_run_id) if r.parent_run_id else None,
                )
                for r in runs
            ],
        )
    except HTTPException as exc:
        logger.error("Error listing runs for workflow %s: %s", workflow_id, exc)
        raise
    except Exception as exc:
        logger.error("Unexpected error listing runs for workflow %s", workflow_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


single_router = APIRouter(
    prefix="/workflows/{workflow_id}/runs/{run_id}",
    tags=["Workflow Control"],
)


@single_router.get("/status", response_model=RunStatusResponse)
async def get_run_status(
    workflow_id: str,
    run_id: str,
    _current_user: CurrentUser,
    service: WorkflowControlService = Depends(get_workflow_control_service),
) -> RunStatusResponse:
    """Return the full status of a single WorkflowRun including per-node breakdown.

    Returns 404 if the run does not exist or does not belong to the given workflow.
    """
    try:
        run, node_runs = await service.get_run_status(workflow_id, run_id)
        return RunStatusResponse(
            run_id=str(run.id),
            workflow_id=str(run.workflow_definition_id),
            status=str(run.status),
            trigger_source=run.trigger_source,
            started_at=run.started_at,
            finished_at=run.finished_at,
            paused_at=run.paused_at,
            error_summary=run.error_summary,
            parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
            node_runs=sorted(
                [
                    NodeRunSummary(
                        node_id=nr.node_id,
                        node_name=nr.node_name,
                        status=str(nr.status),
                        attempt=nr.attempt,
                        started_at=nr.started_at,
                        finished_at=nr.finished_at,
                        error=nr.error,
                    )
                    for nr in node_runs
                ],
                key=lambda x: x.node_name,
            ),
        )
    except HTTPException as exc:
        logger.error("Error getting run status for %s/%s: %s", workflow_id, run_id, exc)
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc
