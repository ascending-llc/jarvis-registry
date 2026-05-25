"""``WorkflowRunner`` — the single entry point for executing a workflow run.

Role in the pipeline
--------------------
``WorkflowRunner`` is the outermost facade.  Callers (API handlers, scripts)
must pre-create a ``WorkflowRun`` document and pass its ID to ``run()``;
all internal coordination is hidden here:

    WorkflowRunner.run(definition_id, user_text, existing_run_id=..., registry_token=...)
        ├─ load WorkflowDefinition from MongoDB
        ├─ load WorkflowRun by existing_run_id → set status=RUNNING + definition_snapshot
        ├─ build_executor_registry()      → resolves MCP/A2A/pool executors
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

import httpx
from agno.exceptions import RunCancelledException
from agno.models.base import Model
from agno.run.cancel import acancel_run as agno_acancel_run
from beanie import PydanticObjectId

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun
from registry_pkgs.workflows.compiler import StepExecutor, compile_workflow, flatten_workflow_nodes
from registry_pkgs.workflows.control import DirectiveQueue, WorkflowCancelledError
from registry_pkgs.workflows.executor_resolver import build_executor_registry
from registry_pkgs.workflows.hitl import hydrate_requirement, serialize_requirement

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
        jwt_config:    JWT signing config used by A2A executors to mint
                       short-lived service-to-agent tokens.
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
        jwt_config: JwtSigningConfig,
        selector_llm: Model | None = None,
        directive_queue: DirectiveQueue | None = None,
        a2a_httpx_client: httpx.AsyncClient | None = None,
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
        self._jwt_config = jwt_config
        # Optional directive queue; when provided, every step executor is wrapped
        # with pause/cancel/retry-backoff logic via with_control().
        self._directive_queue = directive_queue
        self._a2a_httpx_client = a2a_httpx_client

    async def run(
        self,
        definition_id: str,
        user_text: str,
        *,
        registry_token: str,
        user_id: str | None,
        existing_run_id: str,
        injected_outputs: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[WorkflowRun, list[NodeRun]]:
        """Execute a workflow definition and return the completed run + per-node results.

        Callers must pre-create a ``WorkflowRun`` document (status=PENDING) and pass
        its ID via ``existing_run_id``.  This method transitions the run to RUNNING,
        stamps the definition snapshot, then executes; status is always written to a
        terminal value (COMPLETED / FAILED / CANCELLED) before returning or raising.

        Args:
            definition_id:    MongoDB ObjectId string of the WorkflowDefinition.
            user_text:        Top-level input passed as ``workflow.arun(input=...)``.
            registry_token:   User-scoped Bearer token.  Must NOT be shared across users.
            user_id:          User ID for ACL lookup.  ``None`` = unrestricted (scripts only).
            existing_run_id:  ID of the pre-created ``WorkflowRun`` document to drive.
            injected_outputs: Mapping of ``node_id → {"content": ..., "session_state": ...}``
                              for nodes reused from a previous run (retry-from-node).

        Returns:
            A tuple of (WorkflowRun, list[NodeRun]) after the run completes.

        Raises:
            ValueError:      If the WorkflowDefinition or WorkflowRun is not found.
            PermissionError: If the workflow references an A2A agent the caller cannot access.
            Exception:       Re-raises any execution error after marking the run FAILED.
        """
        definition = await WorkflowDefinition.get(definition_id)
        if definition is None:
            raise ValueError(f"WorkflowDefinition {definition_id!r} not found")

        run = await WorkflowRun.get(existing_run_id)
        if run is None:
            raise ValueError(f"WorkflowRun {existing_run_id!r} not found")

        run.status = WorkflowRunStatus.RUNNING
        run.definition_snapshot = definition.model_dump(mode="json")
        await run.save()

        node_names = [n.name for n in flatten_workflow_nodes(definition.nodes) if n.executor_key or n.a2a_pool]
        logger.info(
            "[run=%s] ═══ workflow %r started — %d step(s): %s",
            run.id,
            definition.name,
            len(node_names),
            " → ".join(node_names),
        )

        if self._directive_queue is not None:
            self._directive_queue.register(str(run.id))

        try:
            try:
                executor_registry = await self._build_registry(definition, registry_token, user_id)
            except Exception as exc:
                run.status = WorkflowRunStatus.FAILED
                run.error_summary = str(exc)
                run.finished_at = datetime.now(UTC)
                await run.save()
                logger.error("[run=%s] ✗ failed to build executor registry: %s", run.id, exc, exc_info=True)
                raise
            await self._execute(run, definition, user_text, executor_registry, injected_outputs)
        finally:
            # Always unregister — even on failure — so the queue slot is freed.
            if self._directive_queue is not None:
                self._directive_queue.unregister(str(run.id))

        node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).to_list()
        return run, node_runs

    async def _build_registry(
        self,
        definition: WorkflowDefinition,
        registry_token: str,
        user_id: str | None,
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
            jwt_config=self._jwt_config,
            user_id=user_id,
            pool_nodes=pool_nodes,
            selector_llm=self._selector_llm,
            a2a_httpx_client=self._a2a_httpx_client,
        )

    async def continue_run(
        self,
        *,
        existing_run_id: str,
        registry_token: str,
        user_id: str | None,
    ) -> tuple[WorkflowRun, list[NodeRun]]:
        """Resume a run that is holding at one or more pending requirements.

        Called by ``WorkflowControlService.resolve_requirement`` (via BackgroundTask)
        after the user's decision has been written into ``WorkflowRun.pending_requirements``.

        Flow:
        1. CAS state transition AWAITING_APPROVAL → RUNNING.  If another caller
           already won the race, this method exits silently (no double-resume).
        2. Re-build the agno Workflow from ``run.definition_snapshot`` so any pod
           can resume — we do NOT depend on the in-memory state of whichever pod
           originally returned ``is_paused=True``.
        3. Hydrate ``run.pending_requirements`` back into agno ``StepRequirement``
           objects (with the user's decision fields populated) and clear the field.
        4. Call ``workflow.acontinue_run(...)`` which:
            - reads its own session state from ``agno_workflow_sessions`` Mongo collection
            - applies on_timeout / on_reject routing
            - either runs to completion / failure, or hits the next HITL pause
        5. ``_handle_run_output`` again — pause loops back to step 1 next time the
           user decides; completion / failure is finalized.
        """
        try:
            run_oid = PydanticObjectId(existing_run_id)
        except Exception:
            raise ValueError(f"continue_run: invalid run_id {existing_run_id!r}")

        # CAS: only one continuation wins.  We use raw motor ``update_one`` here.
        collection = self._db_client[self._db_name].get_collection(WorkflowRun.get_settings().name)
        cas_result = await collection.update_one(
            {
                "_id": run_oid,
                "status": WorkflowRunStatus.AWAITING_APPROVAL.value,
            },
            {"$set": {"status": WorkflowRunStatus.RUNNING.value}},
        )
        if cas_result.modified_count == 0:
            logger.info("[run=%s] continue_run: CAS lost (not in AWAITING_APPROVAL), skipping", existing_run_id)
            run = await WorkflowRun.get(run_oid)
            node_runs = await NodeRun.find(NodeRun.workflow_run_id == run_oid).to_list() if run is not None else []
            return run, node_runs  # type: ignore[return-value]

        run = await WorkflowRun.get(run_oid)
        if run is None:
            raise ValueError(f"WorkflowRun {existing_run_id!r} not found")

        if not run.definition_snapshot:
            raise RuntimeError(
                f"WorkflowRun {existing_run_id!r} has no definition_snapshot — cannot rebuild for continue_run"
            )

        # Reconstruct definition from the snapshot to guarantee version determinism
        snapshot_def = WorkflowDefinition(**run.definition_snapshot)

        # Pull the pending requirements out and hydrate back to agno objects.
        pending = list(run.pending_requirements)
        run.pending_requirements = []
        await run.save()
        requirements = [hydrate_requirement(item) for item in pending]

        if self._directive_queue is not None:
            self._directive_queue.register(existing_run_id)

        try:
            executor_registry = await self._build_registry(snapshot_def, registry_token, user_id)
            workflow = compile_workflow(
                snapshot_def,
                run,
                executor_registry=executor_registry,
                db_client=self._db_client,
                db_name=self._db_name,
                directive_queue=self._directive_queue,
            )
            # agno needs its own internal run_id (the UUID it generated inside
            # ``arun``), not our WorkflowRun ObjectId.
            agno_run_id = run.agno_run_id or existing_run_id
            try:
                result = await workflow.acontinue_run(
                    run_id=agno_run_id,
                    session_id=existing_run_id,
                    step_requirements=requirements,
                )
                await self._handle_run_output(run, result)
            except (WorkflowCancelledError, RunCancelledException) as exc:
                await self._finalize_cancel(run, exc)
            except Exception as exc:
                await self._finalize_failure(run, exc)
                raise
        finally:
            if self._directive_queue is not None:
                self._directive_queue.unregister(existing_run_id)

        node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).to_list()
        return run, node_runs

    async def _execute(
        self,
        run: WorkflowRun,
        definition: WorkflowDefinition,
        user_text: str,
        executor_registry: dict[str, StepExecutor],
        injected_outputs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Compile the workflow and run it via agno.

        Handles three terminal outcomes of ``workflow.arun()``:
        - ``is_paused=True``           → HITL pause; persist requirements + return.
        - Normal completion / failure  → ``WorkflowRunSyncer`` already wrote status;
                                         reload via ``run.sync()``.
        - ``WorkflowCancelledError`` / ``RunCancelledException`` → finalize CANCELLED.
        """
        workflow = compile_workflow(
            definition,
            run,
            executor_registry=executor_registry,
            db_client=self._db_client,
            db_name=self._db_name,
            directive_queue=self._directive_queue,
            injected_outputs=injected_outputs,
        )
        try:
            result = await workflow.arun(
                input=user_text,
                session_id=str(run.id),
                # _workflow_run_id lets executor closures reference the current
                # run (e.g. for custom logging or future retry reconstruction).
                session_state={"user_text": user_text, "_workflow_run_id": str(run.id)},
            )
            await self._handle_run_output(run, result)
        except (WorkflowCancelledError, RunCancelledException) as exc:
            await self._finalize_cancel(run, exc)
        except Exception as exc:
            # agno may not call upsert_session on a hard failure; write the error
            # directly so the record is never left dangling as RUNNING.
            await self._finalize_failure(run, exc)
            raise

    async def _handle_run_output(self, run: WorkflowRun, result: Any) -> None:
        """Route the WorkflowRunOutput returned by arun / acontinue_run.

        - If ``result.is_paused``: persist serialized ``step_requirements`` into
          ``WorkflowRun.pending_requirements`` and flip status to AWAITING_APPROVAL.
          The runner coroutine then returns (no busy-waiting; pod-restart safe).
        - Otherwise: trust WorkflowRunSyncer to have already written terminal state,
          and just reload from Mongo so the in-memory ``run`` reflects what
          callers will see.
        """
        if getattr(result, "is_paused", False):
            serialized: list[dict[str, Any]] = []
            for req in getattr(result, "step_requirements", None) or []:
                try:
                    serialized.append(serialize_requirement(req))
                except Exception as exc:
                    logger.exception(
                        "[run=%s] failed to serialize StepRequirement %r — skipping (%s)",
                        run.id,
                        getattr(req, "step_id", "?"),
                        exc,
                    )
            run.status = WorkflowRunStatus.AWAITING_APPROVAL
            run.pending_requirements = serialized
            # Capture agno's internal run_id so ``continue_run`` can locate the
            # persisted RunOutput in agno_workflow_sessions on resume.  We use
            # str() because agno generates UUID strings.
            agno_id = getattr(result, "run_id", None)
            if agno_id:
                run.agno_run_id = str(agno_id)
            await run.save()
            logger.info(
                "[run=%s] ⏸ HITL pause — %d requirement(s) awaiting decision",
                run.id,
                len(serialized),
            )
            return

        await run.sync()

    async def _finalize_cancel(self, run: WorkflowRun, exc: BaseException) -> None:
        """Mark the run CANCELLED and reverse-notify agno (M2)."""
        run.status = WorkflowRunStatus.CANCELLED
        run.error_summary = str(exc)
        run.finished_at = datetime.now(UTC)
        await run.save()
        # Bridge back to agno so its in-memory cancellation state flips too —
        # protects against agno later emitting a WorkflowCompletedEvent that
        # would otherwise overwrite our CANCELLED with COMPLETED via WorkflowRunSyncer.
        try:
            await agno_acancel_run(str(run.id))
        except Exception as inner:
            logger.warning("[run=%s] reverse agno cancel failed: %s", run.id, inner)
        logger.info("[run=%s] ✗ workflow cancelled: %s", run.id, exc)

    async def _finalize_failure(self, run: WorkflowRun, exc: BaseException) -> None:
        """Mark the run FAILED directly (so a half-finished run never stays RUNNING)."""
        run.status = WorkflowRunStatus.FAILED
        run.error_summary = str(exc)
        run.finished_at = datetime.now(UTC)
        await run.save()
        logger.error("[run=%s] ✗ workflow failed: %s", run.id, exc, exc_info=True)
