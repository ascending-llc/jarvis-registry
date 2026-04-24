"""``WorkflowRunner`` — the single entry point for executing a workflow run.

Role in the pipeline
--------------------
``WorkflowRunner`` is the outermost facade.  Callers (API handlers, scripts)
only need to call ``runner.run()``; all internal coordination is hidden here:

    WorkflowRunner.run(definition_id, user_text, registry_token=...)
        ├─ load WorkflowDefinition from MongoDB
        ├─ extract executor_keys + pool_nodes from the definition tree
        ├─ build_executor_registry()      → resolves MCP/A2A/pool executors
        ├─ create + insert WorkflowRun    (status=RUNNING)
        ├─ compile_workflow()             → agno Workflow  (compiler.py)
        ├─ workflow.arun()                → executes steps, triggers WorkflowRunSyncer
        ├─ run.sync()                     → reload final status written by WorkflowRunSyncer
        └─ return (WorkflowRun, list[NodeRun])

Why registry_token is on run() not __init__
-------------------------------------------
``registry_token`` is scoped to a single user request; it must NOT be shared
across concurrent runs from different users.  All other parameters (LLMs,
registry URL, DB credentials) are service-level constants safe to share.

Error handling
--------------
If agno raises before ``upsert_session`` is called, ``WorkflowRunner`` writes
``status=FAILED`` directly so the record is never left as RUNNING.

Usage::

    runner = WorkflowRunner(
        llm=AwsBedrock(...),
        registry_url="https://jarvis.ascendingdc.com",
        db_client=MongoDB.get_client(),
        db_name="jarvis",
    )
    # Each request passes its own token — no cross-user state leakage.
    run, node_runs = await runner.run(
        definition_id,
        user_text,
        registry_token=current_user.token,
    )
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agno.models.base import Model

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun
from registry_pkgs.workflows.compiler import StepExecutor, compile_workflow, flatten_workflow_nodes
from registry_pkgs.workflows.executor_resolver import build_executor_registry

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Execute a WorkflowDefinition by ID and persist all results.

    This class is designed to be a **long-lived service object** — create one
    per application and reuse it across requests.  The only per-request
    parameter is ``registry_token`` passed to ``run()``.

    Args:
        llm:           Model used by MCP-server executors (e.g. AwsBedrock).
        registry_url:  Base URL of the Jarvis Registry gateway.
        db_client:     pymongo AsyncMongoClient for session + Beanie persistence.
        db_name:       MongoDB database name.
        selector_llm:  Optional cheaper/faster model for A2A pool selection.
                       Falls back to ``llm`` when not provided.
    """

    def __init__(
        self,
        *,
        llm: Model,
        registry_url: str,
        db_client: Any,
        db_name: str,
        selector_llm: Model | None = None,
    ) -> None:
        if db_client is None:
            raise ValueError("WorkflowRunner requires db_client")
        if not db_name:
            raise ValueError("WorkflowRunner requires db_name")

        self._llm = llm
        self._selector_llm = selector_llm  # None → falls back to _llm inside build_executor_registry
        self._registry_url = registry_url
        self._db_client = db_client
        self._db_name = db_name

    async def run(
        self,
        definition_id: str,
        user_text: str,
        *,
        registry_token: str,
        trigger_source: str = "api",
    ) -> tuple[WorkflowRun, list[NodeRun]]:
        """Execute a workflow definition and return the completed run + per-node results.

        Args:
            definition_id:  MongoDB ObjectId string of the WorkflowDefinition.
            user_text:      Top-level input passed as ``workflow.arun(input=...)``.
            registry_token: User-scoped Bearer token.  Used by the gateway to
                            authenticate MCP / A2A proxy calls on behalf of the
                            end user.  Must NOT be shared across different users.
            trigger_source: Label stored on WorkflowRun (e.g. ``"api"``, ``"script"``).

        Returns:
            A tuple of (WorkflowRun, list[NodeRun]) after the run completes.

        Raises:
            ValueError:  If the WorkflowDefinition is not found.
            Exception:   Re-raises any agno execution error after marking the run FAILED.
        """
        definition = await WorkflowDefinition.get(definition_id)
        if definition is None:
            raise ValueError(f"WorkflowDefinition {definition_id!r} not found")

        executor_registry = await self._build_registry(definition, registry_token)

        run = await self._create_run(definition, user_text, trigger_source)
        await self._execute(run, definition, user_text, executor_registry)

        node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).to_list()
        return run, node_runs

    async def _build_registry(
        self,
        definition: WorkflowDefinition,
        registry_token: str,
    ) -> dict[str, StepExecutor]:
        """Extract executor keys + pool nodes from the definition and resolve them.

        Walks the entire node tree once to collect:
        - ``executor_keys``: keys for fixed MCP / single-A2A steps.
        - ``pool_nodes``:    STEP nodes that delegate via an A2A pool.

        Both lists are then passed to ``build_executor_registry`` which queries
        MongoDB and constructs the corresponding executor closures.
        """
        all_nodes = flatten_workflow_nodes(definition.nodes)
        # Collect unique executor_keys (pool nodes use a synthetic key, not this list).
        executor_keys = list(dict.fromkeys(n.executor_key for n in all_nodes if n.executor_key))
        pool_nodes = [n for n in all_nodes if n.a2a_pool]

        logger.debug(
            "definition %r: executor_keys=%r  pool_nodes=%r",
            definition.name,
            executor_keys,
            [n.name for n in pool_nodes],
        )

        return await build_executor_registry(
            executor_keys,
            llm=self._llm,
            registry_url=self._registry_url,
            registry_token=registry_token,
            pool_nodes=pool_nodes,
            selector_llm=self._selector_llm,
        )

    async def _create_run(
        self,
        definition: WorkflowDefinition,
        user_text: str,
        trigger_source: str,
    ) -> WorkflowRun:
        """Insert a new WorkflowRun document with status RUNNING."""
        run = WorkflowRun(
            workflow_definition_id=definition.id,
            status=WorkflowRunStatus.RUNNING,
            trigger_source=trigger_source,
            initial_input={"user_text": user_text},
            # Snapshot the definition at run time so retry/audit always has the
            # exact node tree that was executed, even if the definition is later edited.
            definition_snapshot=definition.model_dump(mode="json"),
        )
        await run.insert()
        logger.info("WorkflowRun id=%s created for definition %r", run.id, definition.name)
        return run

    async def _execute(
        self,
        run: WorkflowRun,
        definition: WorkflowDefinition,
        user_text: str,
        executor_registry: dict[str, StepExecutor],
    ) -> None:
        """Compile the workflow and run it via agno.

        On success, ``run.sync()`` reloads the status written by WorkflowRunSyncer.
        On failure, writes FAILED directly so the record is never left as RUNNING.
        """
        workflow = compile_workflow(
            definition,
            run,
            executor_registry=executor_registry,
            db_client=self._db_client,
            db_name=self._db_name,
        )
        try:
            await workflow.arun(
                input=user_text,
                # _workflow_run_id lets executor closures reference the current
                # run (e.g. for custom logging or future retry reconstruction).
                session_state={"user_text": user_text, "_workflow_run_id": str(run.id)},
            )
            # Reload state written by WorkflowRunSyncer so callers see the latest status.
            await run.sync()
        except Exception as exc:
            # agno may not call upsert_session on a hard failure; write the error
            # directly so the record is never left dangling as RUNNING.
            run.status = WorkflowRunStatus.FAILED
            run.error_summary = str(exc)
            run.finished_at = datetime.now(UTC)
            await run.save()
            logger.error("WorkflowRun %s failed: %s", run.id, exc, exc_info=True)
            raise
