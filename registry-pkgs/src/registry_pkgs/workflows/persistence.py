from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, override

from agno.db.mongo.async_mongo import AsyncMongoDb
from agno.run.base import RunStatus
from agno.run.workflow import WorkflowRunOutput
from agno.session import Session, WorkflowSession
from agno.workflow import StepOutput
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.database.decorators import get_current_session
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


class WorkflowRunSyncer(AsyncMongoDb):
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

    @override
    async def upsert_session(
        self,
        session: Session,
        deserialize: bool | None = True,
    ) -> Session | dict[str, Any] | None:
        """Persist agno session, then mirror the latest run into Beanie."""
        result = await super().upsert_session(session, deserialize=deserialize)
        if isinstance(session, WorkflowSession) and session.runs:
            session_data: dict[str, Any] = session.session_data or {}
            try:
                await self._sync_to_beanie(session.runs[-1], session_data=session_data)
            except Exception as e:
                logger.exception(
                    "WorkflowRunSyncer: failed to sync run %s to Beanie, error: %s", self._workflow_run.id, e
                )
        return result

    @override
    async def upsert_sessions(
        self,
        sessions: list[Session],
        deserialize: bool | None = True,
        preserve_updated_at: bool = False,
    ) -> list[Session | dict[str, Any]]:
        message = "WorkflowRunSyncer should not call upsert_sessions at all."
        logger.error(message)
        raise RuntimeError(message)

    async def _sync_to_beanie(
        self,
        run_output: WorkflowRunOutput,
        session_data: dict[str, Any] | None = None,
    ) -> None:
        """Write WorkflowRun status and per-step NodeRuns from WorkflowRunOutput."""
        step_outputs = _flatten_step_results(run_output.step_results)
        final_status = _resolve_workflow_run_status(run_output, step_outputs)
        active_session = get_current_session()

        if final_status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED}:
            if active_session is not None:
                await self._write_run_and_nodes(
                    run_output,
                    step_outputs,
                    session_data=session_data or {},
                    session=active_session,
                )
                return

            # Terminal state must be written atomically when no ambient transaction exists.
            async with await self.db_client.start_session() as mongo_session:
                async with mongo_session.start_transaction():
                    await self._write_run_and_nodes(
                        run_output,
                        step_outputs,
                        session_data=session_data or {},
                        session=mongo_session,
                    )
            return

        # Non-terminal state can be synced outside a transaction, but should still
        # participate in any ambient transaction when one exists.
        await self._write_run_and_nodes(
            run_output,
            step_outputs,
            session_data=session_data or {},
            session=active_session,
        )

    async def _write_run_and_nodes(
        self,
        run_output: WorkflowRunOutput,
        step_outputs: list[StepOutput],
        session_data: dict[str, Any],
        session: AsyncClientSession | None,
    ) -> None:
        await self._update_workflow_run(run_output, step_outputs, session=session)
        for step_output in step_outputs:
            await self._upsert_node_run(
                step_output,
                session_data=session_data,
                session=session,
            )

    async def _update_workflow_run(
        self,
        run_output: WorkflowRunOutput,
        step_outputs: list[StepOutput] | None = None,
        session: AsyncClientSession | None = None,
    ) -> WorkflowRunStatus:
        run = self._workflow_run
        mapped_status = _resolve_workflow_run_status(run_output, step_outputs or [])
        run.status = mapped_status
        if mapped_status in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED}:
            if run.finished_at is None:
                run.finished_at = datetime.now(UTC)
        else:
            run.finished_at = None
        if run_output.content is not None:
            run.final_output = {"content": str(run_output.content)}
        await run.save(session=session)
        logger.info(
            "WorkflowRun %s → status=%s",
            run.id,
            run.status,
        )
        return run.status

    async def _upsert_node_run(
        self,
        step_output: StepOutput,
        session_data: dict[str, Any] | None = None,
        session: AsyncClientSession | None = None,
    ) -> None:
        """Upsert a NodeRun from a StepOutput."""
        step_name = step_output.step_name or ""
        node = self._node_by_name.get(step_name)
        node_id: str = node.id if node else (step_output.step_id or step_name)
        run_id = self._workflow_run.id

        node_run = await NodeRun.find_one(
            NodeRun.workflow_run_id == run_id,
            NodeRun.node_id == node_id,
            session=session,
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

        if session_data:
            # Read the same key written by _make_a2a_pool_executor so the
            # selected agent is persisted for retry reconstruction.
            selected = session_data.get(f"a2a_target_{step_name}")
            if selected:
                node_run.selected_a2a_key = str(selected)

        await node_run.save(session=session)
        logger.debug(
            "NodeRun %s (%s) → %s (selected_a2a=%r)", node_id, step_name, node_run.status, node_run.selected_a2a_key
        )


def _flatten_step_results(results: list[Any]) -> list[StepOutput]:
    """Flatten nested step_results into a single list of StepOutput."""
    flat: list[StepOutput] = []
    for item in results or []:
        if isinstance(item, list):
            flat.extend(_flatten_step_results(item))
        elif isinstance(item, StepOutput):
            flat.append(item)
    return flat


def _resolve_workflow_run_status(
    run_output: WorkflowRunOutput,
    step_outputs: list[StepOutput],
) -> WorkflowRunStatus:
    mapped_status = _STATUS_MAP.get(run_output.status, WorkflowRunStatus.FAILED)
    if any(not step_output.success for step_output in step_outputs):
        return WorkflowRunStatus.FAILED
    return mapped_status
