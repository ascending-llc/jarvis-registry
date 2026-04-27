"""``WorkflowRunner`` — the persisted entry point for running a workflow.

Role in the pipeline
--------------------
``WorkflowRunner`` is the outermost facade.  Callers (scripts, API handlers)
only need to call ``runner.run()``; everything else is handled internally:

    WorkflowRunner.run(definition_id, user_text)
        ├─ load WorkflowDefinition from MongoDB
        ├─ create + insert WorkflowRun (status=RUNNING)
        ├─ compile_workflow()            → agno Workflow  (compiler.py)
        ├─ workflow.arun()               → executes steps, calls WorkflowRunSyncer
        ├─ run.sync()                    → reload final status written by WorkflowRunSyncer
        └─ return (WorkflowRun, list[NodeRun])

Error handling
--------------
If agno raises before ``upsert_session`` is called, ``WorkflowRunner`` writes
``status=FAILED`` directly so the record is never left as RUNNING.

Usage::

    runner = WorkflowRunner(
        executor_registry=executor_registry,   # dict[str, WorkflowExecutor] — MCP-backed agents
        db_client=MongoDB.get_client(),
        db_name="jarvis",
    )
    run, node_runs = await runner.run(definition_id, user_text)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun
from registry_pkgs.workflows.compiler import WorkflowExecutor, compile_workflow

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Run a WorkflowDefinition by ID and persist the results.

    Handles WorkflowRun creation, agno execution, and error fallback so
    callers never have to touch those details directly.

    Args:
        executor_registry: Maps executor_key strings to async executor functions.
        db_client:         pymongo AsyncMongoClient for agno session + Beanie persistence.
        db_name:           MongoDB database name.
    """

    def __init__(
        self,
        executor_registry: dict[str, WorkflowExecutor],
        db_client: Any,
        db_name: str,
    ) -> None:
        if db_client is None:
            raise ValueError("WorkflowRunner requires db_client")
        if not db_name:
            raise ValueError("WorkflowRunner requires db_name")
        self._executor_registry = executor_registry
        self._db_client = db_client
        self._db_name = db_name

    async def run(
        self,
        definition_id: str,
        user_text: str,
        *,
        trigger_source: str = "script",
    ) -> tuple[WorkflowRun, list[NodeRun]]:
        """Execute a workflow definition and return the completed run + per-node results.

        Args:
            definition_id:  MongoDB ObjectId string of the WorkflowDefinition.
            user_text:      Top-level input passed as ``workflow.arun(input=...)``.
            trigger_source: Label stored on the WorkflowRun (e.g. "script", "api").

        Returns:
            A tuple of (WorkflowRun, list[NodeRun]) after the run completes.

        Raises:
            ValueError:  If the WorkflowDefinition is not found.
            Exception:   Re-raises any agno execution error after marking the run FAILED.
        """
        definition = await WorkflowDefinition.get(definition_id)
        if definition is None:
            raise ValueError(f"WorkflowDefinition {definition_id!r} not found")

        run = await self._create_run(definition, user_text, trigger_source)
        await self._execute(run, definition, user_text)
        node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).to_list()
        return run, node_runs

    async def _create_run(
        self,
        definition: WorkflowDefinition,
        user_text: str,
        trigger_source: str,
    ) -> WorkflowRun:
        run = WorkflowRun(
            workflow_definition_id=definition.id,
            status=WorkflowRunStatus.RUNNING,
            trigger_source=trigger_source,
            initial_input={"user_text": user_text},
            definition_snapshot=definition.model_dump(mode="json"),
        )
        await run.insert()
        logger.info("Created WorkflowRun id=%s for definition %r", run.id, definition.name)
        return run

    async def _execute(
        self,
        run: WorkflowRun,
        definition: WorkflowDefinition,
        user_text: str,
    ) -> None:
        workflow = compile_workflow(
            definition,
            run,
            executor_registry=self._executor_registry,
            db_client=self._db_client,
            db_name=self._db_name,
        )
        try:
            await workflow.arun(
                input=user_text,
                session_state={"user_text": user_text},
            )
            # Reload state written by WorkflowRunSyncer so callers see the latest status.
            await run.sync()
        except Exception as exc:
            # agno may not call upsert_session on a hard failure; write the error
            # directly so the record is never left as RUNNING.
            run.status = WorkflowRunStatus.FAILED
            run.error_summary = str(exc)
            run.finished_at = datetime.now(UTC)
            await run.save()
            logger.error("WorkflowRun %s failed: %s", run.id, exc, exc_info=True)
            raise
