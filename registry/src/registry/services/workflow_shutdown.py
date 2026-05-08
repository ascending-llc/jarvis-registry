import logging
from datetime import UTC, datetime

from beanie import PydanticObjectId
from beanie.operators import In, Set
from pydantic import BaseModel

from registry_pkgs.models.enums import NodeRunStatus, WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowRun

logger = logging.getLogger(__name__)


class _WorkflowRunIdProjection(BaseModel):
    id: PydanticObjectId

    class Settings:
        projection = {"_id": 1}


async def cancel_in_flight_runs() -> None:
    """
    Mark in-flight WorkflowRuns and their NodeRuns as CANCELLED on pod shutdown.

        Uses two bulk $set operations so shutdown completes quickly regardless of
        how many runs are in flight.
    """
    now = datetime.now(UTC)
    active_statuses = [WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED]

    active_runs = await WorkflowRun.find(
        In(WorkflowRun.status, active_statuses),
        projection_model=_WorkflowRunIdProjection,
    ).to_list()

    if not active_runs:
        return

    run_ids = [run.id for run in active_runs]

    await WorkflowRun.find(In(WorkflowRun.id, run_ids)).update(
        Set({WorkflowRun.status: WorkflowRunStatus.CANCELLED, WorkflowRun.finished_at: now})
    )
    logger.warning("Pod shutdown: cancelled %d in-flight WorkflowRun(s)", len(run_ids))

    active_node_statuses = [NodeRunStatus.PENDING, NodeRunStatus.RUNNING]
    await NodeRun.find(
        In(NodeRun.workflow_run_id, run_ids),
        In(NodeRun.status, active_node_statuses),
    ).update(Set({NodeRun.status: NodeRunStatus.CANCELLED, NodeRun.finished_at: now}))
    logger.warning("Pod shutdown: cancelled NodeRun(s) for %d workflow run(s)", len(run_ids))
