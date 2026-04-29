from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TriggerRunRequest(BaseModel):
    """Request body for the workflow run trigger endpoint.

    Attributes:
        user_text: Free-form prompt text forwarded to the workflow's first step.
    """

    user_text: str = Field(..., description="Prompt text to send to the workflow")


class TriggerRunResponse(BaseModel):
    """Response returned when a new workflow run is successfully triggered.

    Attributes:
        run_id:      The newly created WorkflowRun ID.
        workflow_id: The parent WorkflowDefinition ID.
        status:      Initial status — always ``"pending"`` at trigger time.
    """

    run_id: str
    workflow_id: str
    status: str


class RetryRequest(BaseModel):
    """Request body for the retry endpoint.

    Attributes:
        from_node_id: The ``WorkflowNode.id`` from which re-execution should
                      begin.  All nodes before this one that completed
                      successfully in the original run are replayed from their
                      cached ``NodeRun.output_snapshot`` without calling any
                      external service.
    """

    from_node_id: str = Field(..., description="Node ID to restart execution from")


class DirectiveResponse(BaseModel):
    """Unified response returned by all four control endpoints.

    Attributes:
        run_id:  The ID of the affected WorkflowRun (original or newly-created
                 child run for retry).
        status:  The WorkflowRun status after the directive was applied.
        message: A short human-readable summary of what happened.
    """

    run_id: str
    status: str
    message: str


# ── Status query responses ────────────────────────────────────────────────────


class NodeRunSummary(BaseModel):
    """Per-node execution summary returned inside :class:`RunStatusResponse`.

    Attributes:
        node_id:     Unique node ID from the WorkflowDefinition.
        node_name:   Human-readable step name.
        status:      NodeRunStatus value (pending/running/completed/failed/skipped/cancelled).
        attempt:     Number of attempts made (1-based; 0 means not yet started).
        started_at:  When the first attempt began, or ``None`` if not yet started.
        finished_at: When the node completed (any terminal status), or ``None``.
        error:       Last error message if the node failed, otherwise ``None``.
    """

    node_id: str
    node_name: str
    status: str
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class RunStatusResponse(BaseModel):
    """Full status of a single WorkflowRun including per-node breakdown.

    Returned by ``GET /workflows/{workflow_id}/runs/{run_id}/status``.

    Attributes:
        run_id:        The WorkflowRun ID.
        workflow_id:   The parent WorkflowDefinition ID.
        status:        WorkflowRunStatus value.
        trigger_source: How the run was started (e.g. ``"api"``, ``"retry"``).
        started_at:    When the run was created.
        finished_at:   When the run reached a terminal status, or ``None``.
        paused_at:     When the run was last paused, or ``None``.
        error_summary: Top-level error description if the run failed, otherwise ``None``.
        parent_run_id: Set when this is a child retry run; ``None`` for original runs.
        node_runs:     Per-node execution summaries, ordered by ``node_name``.
    """

    run_id: str
    workflow_id: str
    status: str
    trigger_source: str | None
    started_at: datetime
    finished_at: datetime | None
    paused_at: datetime | None
    error_summary: str | None
    parent_run_id: str | None
    node_runs: list[NodeRunSummary]


class RunSummary(BaseModel):
    """Lightweight summary of a single WorkflowRun used in list responses.

    Returned as elements of :class:`WorkflowRunsStatusResponse`.

    Attributes:
        run_id:        The WorkflowRun ID.
        status:        WorkflowRunStatus value.
        trigger_source: How the run was started.
        started_at:    When the run was created.
        finished_at:   When the run reached a terminal status, or ``None``.
        error_summary: Top-level error if the run failed, otherwise ``None``.
        parent_run_id: Set for child retry runs; ``None`` for original runs.
    """

    run_id: str
    status: str
    trigger_source: str | None
    started_at: datetime
    finished_at: datetime | None
    error_summary: str | None
    parent_run_id: str | None


class WorkflowRunsStatusResponse(BaseModel):
    """All WorkflowRuns for a given WorkflowDefinition, newest first.

    Returned by ``GET /workflows/{workflow_id}/runs/status``.

    Attributes:
        workflow_id: The WorkflowDefinition ID.
        total:       Total number of runs returned.
        runs:        List of run summaries, ordered by ``started_at`` descending.
    """

    workflow_id: str
    total: int
    runs: list[RunSummary]
