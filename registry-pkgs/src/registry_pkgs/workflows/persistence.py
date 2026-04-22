from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agno.db.mongo.async_mongo import AsyncMongoDb
from agno.run.base import RunStatus
from agno.run.workflow import WorkflowRunOutput
from agno.session import WorkflowSession
from agno.workflow import StepOutput

from registry_pkgs.models.enums import NodeRunStatus, WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowNode, WorkflowRun

logger = logging.getLogger(__name__)

_STATUS_MAP: dict[RunStatus, WorkflowRunStatus] = {
    RunStatus.completed: WorkflowRunStatus.COMPLETED,
    RunStatus.error: WorkflowRunStatus.FAILED,
    RunStatus.cancelled: WorkflowRunStatus.FAILED,
    RunStatus.pending: WorkflowRunStatus.RUNNING,
    RunStatus.running: WorkflowRunStatus.RUNNING,
    RunStatus.paused: WorkflowRunStatus.RUNNING,
}


class WorkflowRunSync(AsyncMongoDb):
    """Sync agno workflow sessions into WorkflowRun and NodeRun documents."""

    def __init__(
        self,
        workflow_run: WorkflowRun,
        node_by_name: dict[str, WorkflowNode],
        db_client: Any,
        db_name: str,
        session_collection: str = "agno_workflow_sessions",
    ) -> None:
        super().__init__(
            db_client=db_client,
            db_name=db_name,
            session_collection=session_collection,
        )
        self._workflow_run = workflow_run
        self._node_by_name = node_by_name

    async def upsert_session(
        self,
        session: Any,
        deserialize: bool | None = True,
    ) -> Any:
        """Persist agno session, then mirror the latest run into Beanie."""
        result = await super().upsert_session(session, deserialize=deserialize)
        if isinstance(session, WorkflowSession) and session.runs:
            try:
                await self._sync_to_beanie(session.runs[-1])
            except Exception as e:
                logger.exception(
                    "WorkflowRunSync: failed to sync run %s to Beanie, error: %s", self._workflow_run.id, e
                )
        return result

    async def _sync_to_beanie(self, run_output: WorkflowRunOutput) -> None:
        """Write WorkflowRun status and per-step NodeRuns from WorkflowRunOutput."""
        await self._update_workflow_run(run_output)
        for step_output in _flatten_step_results(run_output.step_results):
            await self._upsert_node_run(step_output)

    async def _update_workflow_run(self, run_output: WorkflowRunOutput) -> None:
        run = self._workflow_run
        mapped_status = _STATUS_MAP.get(run_output.status, WorkflowRunStatus.FAILED)
        run.status = mapped_status
        if mapped_status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED}:
            if run.finished_at is None:
                run.finished_at = datetime.now(UTC)
        else:
            run.finished_at = None
        if run_output.content is not None:
            run.final_output = {"content": str(run_output.content)}
        await run.save()
        logger.info(
            "WorkflowRun %s → status=%s",
            run.id,
            run.status,
        )

    async def _upsert_node_run(self, step_output: StepOutput) -> None:
        """Upsert a NodeRun from a StepOutput."""
        step_name = step_output.step_name or ""
        node = self._node_by_name.get(step_name)
        node_id: str = node.id if node else (step_output.step_id or step_name)
        run_id = self._workflow_run.id

        node_run = await NodeRun.find_one(
            NodeRun.workflow_run_id == run_id,
            NodeRun.node_id == node_id,
        )
        if node_run is None:
            node_run = NodeRun(
                workflow_run_id=run_id,
                node_id=node_id,
                node_name=step_name,
            )

        node_run.status = NodeRunStatus.COMPLETED if step_output.success else NodeRunStatus.FAILED
        node_run.attempt += 1
        node_run.finished_at = datetime.now(UTC)
        node_run.error = step_output.error
        if step_output.content is not None:
            node_run.output_snapshot = {"content": str(step_output.content)}

        await node_run.save()
        logger.debug("NodeRun %s (%s) → %s", node_id, step_name, node_run.status)


def _flatten_step_results(results: list[Any]) -> list[StepOutput]:
    """Flatten nested step_results into a single list of StepOutput."""
    flat: list[StepOutput] = []
    for item in results or []:
        if isinstance(item, list):
            flat.extend(_flatten_step_results(item))
        elif isinstance(item, StepOutput):
            flat.append(item)
    return flat
