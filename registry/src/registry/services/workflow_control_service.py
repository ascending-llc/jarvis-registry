"""
WorkflowControlService — validates and dispatches user directives to live runs.

Responsibilities
----------------
1. Load and ownership-check the ``WorkflowRun`` document.
2. Validate the requested directive via ``WorkflowRunStateMachine.apply_directive``.
3. Persist ``WorkflowRun.pending_directive`` (and related fields) to MongoDB —
   this is the durable source of truth that survives service restarts.
4. Notify the in-process ``DirectiveQueue`` so the running executor wrapper
   receives the directive without waiting for the next MongoDB poll cycle.
5. For the *retry* directive: build a child ``WorkflowRun`` with
   ``resolved_dependencies`` filled in, then fire-and-forget the runner via
   ``asyncio.create_task``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException

from registry_pkgs.models.enums import (
    NodeRunStatus,
    ResolvedDependencyResolution,
    WorkflowDirective,
    WorkflowNodeType,
    WorkflowRunStateMachine,
    WorkflowRunStatus,
)
from registry_pkgs.models.workflow import NodeRun, ResolvedDependency, WorkflowRun
from registry_pkgs.workflows.compiler import flatten_workflow_nodes
from registry_pkgs.workflows.control import DirectiveQueue

logger = logging.getLogger(__name__)

# Callable type for the async runner — kept as a protocol-style alias to avoid
# importing WorkflowRunner here (that would pull in heavy agno dependencies at
# import time, slowing down tests that only need the service).
RunnerCallable = Callable[..., object]


class WorkflowControlService:
    """Send pause / resume / cancel / retry directives to workflow runs.

    Args:
        directive_queue: The app-scoped in-process signal bus shared with the runner.
        runner_factory:  Zero-argument callable that returns a ready-to-use
                         ``WorkflowRunner`` instance.  Called lazily only when a
                         retry creates a new child run.  May be ``None`` in
                         environments where retry is not supported.
    """

    def __init__(
        self,
        directive_queue: DirectiveQueue,
        runner_factory: Callable[[], RunnerCallable] | None = None,
    ) -> None:
        self._queue = directive_queue
        self._runner_factory = runner_factory

    async def trigger_run(
        self,
        workflow_definition_id: str,
        user_text: str,
        *,
        registry_token: str,
        user_id: str | None,
    ) -> WorkflowRun:
        """Start a new WorkflowRun for the given WorkflowDefinition.

        Creates a ``WorkflowRun`` document with status ``PENDING`` and fires the
        runner as a background ``asyncio`` task so this method returns immediately
        with the new run's ID.

        Args:
            workflow_definition_id: The WorkflowDefinition ObjectId string.
            user_text:              Prompt forwarded to the workflow's first step.
            registry_token:         User-scoped Bearer token for the runner.
            user_id:                User ID for ACL lookup inside the runner.

        Raises:
            HTTPException(400): ``workflow_definition_id`` is not a valid ObjectId.
            HTTPException(404): WorkflowDefinition not found.
            HTTPException(501): Runner factory not configured on this instance.
        """
        if self._runner_factory is None:
            raise HTTPException(status_code=501, detail="Workflow runner is not configured on this instance")

        try:
            def_oid = PydanticObjectId(workflow_definition_id)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid workflow_id {workflow_definition_id!r}")

        from registry_pkgs.models.workflow import WorkflowDefinition  # local import avoids circular deps

        definition = await WorkflowDefinition.get(def_oid)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"WorkflowDefinition {workflow_definition_id!r} not found")

        run = WorkflowRun(
            workflow_definition_id=def_oid,
            status=WorkflowRunStatus.PENDING,
            trigger_source="api",
            initial_input={"user_text": user_text},
        )
        await run.insert()
        logger.info("WorkflowRun %s created for definition %s", run.id, workflow_definition_id)

        runner = self._runner_factory()
        asyncio.create_task(  # fire-and-forget; HTTP response returns immediately
            runner.run(  # type: ignore[union-attr]
                workflow_definition_id,
                user_text,
                registry_token=registry_token,
                user_id=user_id,
                trigger_source="api",
                existing_run_id=str(run.id),
            )
        )
        return run

    async def send_pause(self, workflow_definition_id: str, run_id: str) -> WorkflowRun:
        """Pause a RUNNING workflow run.

        Idempotent: if the run is already PAUSED, returns successfully without
        side effects.
        """
        run = await self._load_run(workflow_definition_id, run_id)
        if run.pending_directive == WorkflowDirective.CANCEL:
            raise HTTPException(status_code=400, detail="Cannot pause a run with a pending cancel directive")
        if run.pending_directive == WorkflowDirective.RESUME:
            raise HTTPException(status_code=400, detail="Cannot pause a run with a pending resume directive")
        new_status = _apply(run, WorkflowDirective.PAUSE)

        if new_status == run.status:
            return run

        run.pending_directive = WorkflowDirective.PAUSE
        await run.save()
        self._queue.put(run_id, WorkflowDirective.PAUSE)
        logger.info("WorkflowRun %s pause requested", run_id)

        return run

    async def send_resume(self, workflow_definition_id: str, run_id: str) -> WorkflowRun:
        """Resume a PAUSED workflow run."""
        run = await self._load_run(workflow_definition_id, run_id)
        if run.pending_directive == WorkflowDirective.RESUME:
            raise HTTPException(status_code=400, detail="Run already has a pending resume directive")
        _apply(run, WorkflowDirective.RESUME)
        run.pending_directive = WorkflowDirective.RESUME
        await run.save()
        self._queue.put(run_id, WorkflowDirective.RESUME)
        logger.info("WorkflowRun %s resume requested", run_id)
        return run

    async def send_cancel(self, workflow_definition_id: str, run_id: str) -> WorkflowRun:
        """Cancel a RUNNING or PAUSED workflow run.

        Idempotent: if the run is already CANCELLED, returns successfully without
        side effects.
        """
        run = await self._load_run(workflow_definition_id, run_id)
        if run.pending_directive == WorkflowDirective.CANCEL:
            return run
        new_status = _apply(run, WorkflowDirective.CANCEL)

        if new_status == run.status:
            return run

        run.pending_directive = WorkflowDirective.CANCEL
        await run.save()
        self._queue.put(run_id, WorkflowDirective.CANCEL)
        logger.info("WorkflowRun %s cancel requested", run_id)

        return run

    async def send_retry(
        self,
        workflow_definition_id: str,
        run_id: str,
        from_node_id: str,
        *,
        registry_token: str,
        user_id: str | None,
    ) -> WorkflowRun:
        """Retry a finished run from *from_node_id* onwards.

        Creates a child ``WorkflowRun`` whose ``resolved_dependencies`` describe
        which nodes are replayed from the parent's cached outputs and which are
        re-executed for real.  The runner is started as a background asyncio task
        so this method returns the new (PENDING) child run immediately.

        Args:
            workflow_definition_id: Must match ``run.workflow_definition_id``.
            run_id:                 The finished parent run.
            from_node_id:           Node ID within the definition from which
                                    re-execution should start.
            registry_token:         User-scoped Bearer token forwarded to the runner.
            user_id:                User ID for ACL lookup forwarded to the runner.
        """
        if self._runner_factory is None:
            raise HTTPException(status_code=501, detail="Retry is not configured on this instance")

        parent_run = await self._load_run(workflow_definition_id, run_id)
        _apply(parent_run, WorkflowDirective.RETRY)  # raises 400 on invalid state

        if not parent_run.definition_snapshot:
            raise HTTPException(status_code=409, detail="Run has no definition snapshot; cannot retry")

        # Build resolved_dependencies and collect outputs to inject.
        from registry_pkgs.models.workflow import WorkflowDefinition  # local import

        definition = WorkflowDefinition(**parent_run.definition_snapshot)
        all_nodes = flatten_workflow_nodes(definition.nodes)

        from_index = next((i for i, n in enumerate(all_nodes) if n.id == from_node_id), None)
        if from_index is None:
            raise HTTPException(
                status_code=400,
                detail=f"Node {from_node_id!r} not found in workflow definition",
            )

        parent_node_runs = await NodeRun.find(NodeRun.workflow_run_id == parent_run.id).to_list()
        node_run_by_id: dict[str, NodeRun] = {nr.node_id: nr for nr in parent_node_runs}

        resolved_deps: list[ResolvedDependency] = []
        injected_outputs: dict[str, dict[str, Any]] = {}

        for i, node in enumerate(all_nodes):
            if node.node_type != WorkflowNodeType.STEP:
                continue
            nr = node_run_by_id.get(node.id)
            if i < from_index and nr and nr.status == NodeRunStatus.COMPLETED:
                resolved_deps.append(
                    ResolvedDependency(
                        node_id=node.id,
                        resolution=ResolvedDependencyResolution.REUSE_PREVIOUS_OUTPUT,
                        source_node_run_id=nr.id,
                    )
                )
                if nr.output_snapshot:
                    injected_outputs[node.id] = {
                        "content": nr.output_snapshot.get("content", ""),
                        "session_state": nr.session_state_snapshot or {},
                    }
            else:
                resolved_deps.append(
                    ResolvedDependency(
                        node_id=node.id,
                        resolution=ResolvedDependencyResolution.RERUN,
                    )
                )

        child_run = WorkflowRun(
            workflow_definition_id=parent_run.workflow_definition_id,
            status=WorkflowRunStatus.PENDING,
            trigger_source="retry",
            initial_input=parent_run.initial_input,
            definition_snapshot=parent_run.definition_snapshot,
            parent_run_id=parent_run.id,
            resolved_dependencies=resolved_deps,
        )
        await child_run.insert()
        logger.info(
            "WorkflowRun %s: created child retry run %s from node %r",
            run_id,
            child_run.id,
            from_node_id,
        )

        user_text: str = (parent_run.initial_input or {}).get("user_text", "")
        runner = self._runner_factory()

        asyncio.create_task(  # fire-and-forget; HTTP response returns immediately
            runner.run(  # type: ignore[union-attr]
                str(parent_run.workflow_definition_id),
                user_text,
                registry_token=registry_token,
                user_id=user_id,
                trigger_source="retry",
                existing_run_id=str(child_run.id),
                injected_outputs=injected_outputs,
            )
        )
        return child_run

    async def get_run_status(
        self,
        workflow_definition_id: str,
        run_id: str,
    ) -> tuple[WorkflowRun, list[NodeRun]]:
        """Return the WorkflowRun and its per-node NodeRuns.

        Args:
            workflow_definition_id: Must match ``run.workflow_definition_id``.
            run_id:                 The WorkflowRun to query.

        Raises:
            HTTPException(404): Run not found or belongs to a different workflow.
        """
        run = await self._load_run(workflow_definition_id, run_id)
        node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).to_list()
        return run, node_runs

    async def get_workflow_runs(self, workflow_definition_id: str) -> list[WorkflowRun]:
        """Return all WorkflowRuns for a workflow definition, newest first.

        Args:
            workflow_definition_id: The WorkflowDefinition ObjectId string.

        Raises:
            HTTPException(400): If ``workflow_definition_id`` is not a valid ObjectId.
        """
        try:
            def_oid = PydanticObjectId(workflow_definition_id)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow_id {workflow_definition_id!r}",
            )
        return await WorkflowRun.find(WorkflowRun.workflow_definition_id == def_oid).sort("-started_at").to_list()

    async def _load_run(self, workflow_definition_id: str, run_id: str) -> WorkflowRun:
        """Load and ownership-verify a WorkflowRun.

        Raises:
            HTTPException(404): Run not found or belongs to a different workflow.
        """
        try:
            oid = PydanticObjectId(run_id)
        except Exception:
            raise HTTPException(status_code=404, detail=f"WorkflowRun {run_id!r} not found")

        run = await WorkflowRun.get(oid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"WorkflowRun {run_id!r} not found")

        if str(run.workflow_definition_id) != workflow_definition_id:
            raise HTTPException(
                status_code=404,
                detail=f"WorkflowRun {run_id!r} does not belong to workflow {workflow_definition_id!r}",
            )
        return run


def _apply(run: WorkflowRun, directive: WorkflowDirective) -> WorkflowRunStatus:
    """Wrap WorkflowRunStateMachine.apply_directive and convert ValueError → HTTP 400."""
    try:
        return WorkflowRunStateMachine.apply_directive(run.status, directive)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
