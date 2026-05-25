"""
WorkflowControlService — validates and dispatches user directives to live runs.

Responsibilities
----------------
1. Load and verify the ``WorkflowRun`` document belongs to the given workflow.
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
from datetime import UTC, datetime
from typing import Any, Protocol

from agno.run.cancel import acancel_run as agno_acancel_run
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.utils.crypto_utils import generate_service_jwt
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.enums import (
    NodeRunStatus,
    RequirementResolution,
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


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget runner tasks.

    Python silently discards unhandled task exceptions; this callback ensures
    any exception that escapes the runner's own error handling is at least
    logged so the failure is visible in application logs.
    """
    if not task.cancelled() and (exc := task.exception()):
        logger.error("Background workflow task raised unhandled exception: %s", exc, exc_info=exc)


def _fire_background(coro: Any) -> asyncio.Task:
    """Fire-and-forget a runner coroutine — without the callback Python swallows
    any exception silently, so always go through this helper."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_log_task_exception)
    return task


class _HasRun(Protocol):
    """Structural interface for WorkflowRunner — avoids importing agno at module load time."""

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
        pass

    async def continue_run(
        self,
        *,
        existing_run_id: str,
        registry_token: str,
        user_id: str | None,
    ) -> tuple[WorkflowRun, list[NodeRun]]:
        """Resume a run that hit an HITL pause after the user decided."""
        pass


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
        runner_factory: Callable[[], _HasRun] | None = None,
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
            triggering_user_id=user_id,
        )
        await run.insert()
        logger.info("WorkflowRun %s created for definition %s", run.id, workflow_definition_id)

        runner = self._runner_factory()
        _fire_background(
            runner.run(
                workflow_definition_id,
                user_text,
                registry_token=registry_token,
                user_id=user_id,
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
        if run.pending_directive == WorkflowDirective.CANCEL:
            raise HTTPException(status_code=400, detail="Cannot resume a run with a pending cancel directive")
        if run.pending_directive == WorkflowDirective.RESUME:
            raise HTTPException(status_code=400, detail="Run already has a pending resume directive")
        _apply(run, WorkflowDirective.RESUME)
        run.pending_directive = WorkflowDirective.RESUME
        await run.save()
        self._queue.put(run_id, WorkflowDirective.RESUME)
        logger.info("WorkflowRun %s resume requested", run_id)
        return run

    async def resolve_requirement(
        self,
        workflow_definition_id: str,
        run_id: str,
        *,
        step_id: str,
        resolution: RequirementResolution,
        feedback: str | None = None,
        edited_output: Any | None = None,
        user_input: dict[str, Any] | None = None,
        selected_choices: list[str] | None = None,
    ) -> WorkflowRun:
        """Resolve one pending requirement on an AWAITING_APPROVAL run.

        1. **Validate**: run must exist + be in AWAITING_APPROVAL + have a matching
           pending requirement with ``confirmed is None``.
        2. **Compatibility check**: the chosen ``resolution`` must match the
           requirement's capability flags (e.g. you can't send ``USER_INPUT`` to a
           requirement that only needs confirmation).
        3. **Atomic decision write**: uses MongoDB ``array_filters`` to update
           the single matching pending_requirement element in place.  Concurrent
           decisions on *different* stepIds are independent; concurrent decisions
           on the *same* stepId result in 409 (only one wins).
        4. **Trigger BackgroundTask continue_run**: the runner re-builds the agno
           Workflow on whichever pod handles the task and calls ``acontinue_run``.
           The HTTP request returns immediately — no waiting for the actual resume.
        """
        run = await self._load_run(workflow_definition_id, run_id)

        if run.status != WorkflowRunStatus.AWAITING_APPROVAL:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Run is not awaiting approval (current status: {run.status}). "
                    "Refresh and verify the run state before retrying."
                ),
            )

        target = _find_pending_requirement(run, step_id)
        _validate_resolution_compatibility(target, resolution)

        update_fields = _build_resolution_update(resolution, feedback, edited_output, user_input, selected_choices)
        await _atomic_write_decision(run, step_id, update_fields)

        logger.info("WorkflowRun %s requirement %s resolved as %s", run_id, step_id, resolution.value)

        self._trigger_resume(run)

        return await self._load_run(workflow_definition_id, run_id)

    def _trigger_resume(self, run: WorkflowRun) -> None:
        """Fire ``continue_run`` in the background to resume an AWAITING_APPROVAL run.

        Shared by ``resolve_requirement`` (user decided) and ``get_run_status``
        (a pending requirement timed out). The runner re-builds the agno Workflow
        from the snapshot and calls ``acontinue_run`` — which both applies the
        user's decision and, for any requirement past its ``timeout_at``, applies
        agno's ``on_timeout`` policy. ``continue_run`` performs a CAS so duplicate
        triggers (e.g. concurrent polls) are harmless — only one wins.
        """
        if self._runner_factory is None:
            logger.error(
                "_trigger_resume: runner_factory not configured — cannot continue_run for %s",
                run.id,
            )
            return
        token = _prepare_resume_credentials(run)
        runner = self._runner_factory()
        _fire_background(
            runner.continue_run(
                existing_run_id=str(run.id),
                registry_token=token,
                user_id=run.triggering_user_id,
            )
        )

    async def send_cancel(self, workflow_definition_id: str, run_id: str) -> WorkflowRun:
        """Cancel a RUNNING / PAUSED / AWAITING_APPROVAL workflow run.

        Dual-signal design:
        1. ``WorkflowRun.pending_directive = CANCEL`` (durable truth source).
        2. ``DirectiveQueue.put(CANCEL)`` (in-process fast path for wait-loop).
        3. ``agno.run.cancel.acancel_run(run_id)`` — routes through
           ``MongoBackedCancellationManager`` which ALSO writes ``pending_directive=CANCEL``
           so any agno-internal code path that calls ``raise_if_cancelled`` also sees it.

        The triple-redundancy protects against #7929 (agno acontinue_run +
        external_execution bypasses raise_if_cancelled) and ensures the wait-loop
        wrapper has a safety net.

        Idempotent: cancelling an already-cancelled run returns 200.
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

        # Reverse-bridge to agno's cancellation manager so any in-flight agno code
        # path (continue_run, external_execution, streaming) also flips.
        try:
            await agno_acancel_run(run_id)
        except Exception as exc:
            logger.warning("WorkflowRun %s: agno acancel_run bridge failed: %s", run_id, exc)

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
            # Inherit the parent's version so the retry replays the same definition
            # snapshot deterministically and reports a consistent workflow_version.
            workflow_version=parent_run.workflow_version,
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
        _fire_background(
            runner.run(
                str(parent_run.workflow_definition_id),
                user_text,
                registry_token=registry_token,
                user_id=user_id,
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

        Side effect: if the run is AWAITING_APPROVAL and a pending requirement has
        passed its ``timeout_at``, this lazily nudges ``continue_run`` so agno can
        apply the gate's ``on_timeout`` policy (agno checks timeouts only at
        continue-time, never via a background timer). The returned run still shows
        AWAITING_APPROVAL — the resume completes asynchronously and surfaces on the
        next poll.
        """
        run = await self._load_run(workflow_definition_id, run_id)
        if run.status == WorkflowRunStatus.AWAITING_APPROVAL and _has_timed_out_requirement(run):
            logger.info("WorkflowRun %s has a timed-out requirement — nudging continue_run", run_id)
            self._trigger_resume(run)
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
        """Load a WorkflowRun and verify it belongs to the requested workflow.

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


def _find_pending_requirement(run: WorkflowRun, step_id: str) -> dict[str, Any]:
    """Locate the still-unresolved requirement (``confirmed is None``) for
    ``step_id``, or raise 404 if it's unknown or already resolved."""
    target = next(
        (r for r in run.pending_requirements if r.get("step_id") == step_id and r.get("confirmed") is None),
        None,
    )
    if not target:
        raise HTTPException(
            status_code=404,
            detail=f"No pending requirement with step_id={step_id!r} (already resolved or unknown)",
        )
    return target


def _has_timed_out_requirement(run: WorkflowRun) -> bool:
    """True if any still-unresolved pending requirement has passed its deadline.

    agno serializes ``StepRequirement.timeout_at`` as an ISO-8601 string (or omits
    it when no timeout was configured). We only detect the expiry here — agno
    applies the actual ``on_timeout`` policy (cancel / skip / approve) itself the
    next time ``continue_run`` runs, so detecting is enough to nudge a resume.
    """
    now = datetime.now(UTC)
    for req in run.pending_requirements:
        if req.get("confirmed") is not None:
            continue
        raw = req.get("timeout_at")
        if not raw:
            continue
        try:
            deadline = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if now >= deadline:
            return True
    return False


async def _atomic_write_decision(
    run: WorkflowRun,
    step_id: str,
    update_fields: dict[str, Any],
) -> None:
    """Write the user's decision into ``pending_requirements`` atomically.

    Three concurrent-safety invariants enforced in a single MongoDB call:
      1. Top-level filter ``status == AWAITING_APPROVAL`` — prevents writing a
         decision to a run that another caller has already advanced.
      2. ``array_filters`` matches only the element whose ``confirmed is None``
         — a second concurrent decision on the same step_id finds nothing.
      3. ``modified_count == 0`` ⇒ 409 — either #1 or #2 failed; the caller
         must refresh and retry.

    Uses raw motor (rather than Beanie) because array_filters is not yet
    surfaced cleanly through the ODM.
    """
    set_payload = {f"pending_requirements.$[elem].{k}": v for k, v in update_fields.items()}
    db = MongoDB.get_database()
    collection = db.get_collection(WorkflowRun.get_settings().name)
    raw_result = await collection.update_one(
        {"_id": run.id, "status": WorkflowRunStatus.AWAITING_APPROVAL.value},
        {"$set": set_payload},
        array_filters=[{"elem.step_id": step_id, "elem.confirmed": None}],
    )
    if raw_result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Requirement was concurrently resolved or run state changed; refresh and retry",
        )


def _prepare_resume_credentials(run: WorkflowRun) -> str:
    """Re-mint a short-lived service JWT representing the triggering user so
    downstream MCP/A2A calls inside the resumed step authenticate as them.

    We persist only non-sensitive identity (user_id / username / scopes) on the
    run — never the raw bearer token — and re-sign a fresh token at resume time.
    Returns ``""`` when no ``triggering_user_id`` was captured (e.g. script-driven
    runs); auth-required steps will then 401, which is non-fatal to the resume.
    """
    if not run.triggering_user_id:
        return ""
    return generate_service_jwt(
        user_id=run.triggering_user_id,
        username=run.triggering_username,
        scopes=run.triggering_scopes or [],
    )


def _validate_resolution_compatibility(
    requirement: dict[str, Any],
    resolution: RequirementResolution,
) -> None:
    """Reject decisions that don't match the requirement's capability flags.

    Examples:
    - ``USER_INPUT`` resolution on a requirement that only needs confirmation → 400
    - ``EDIT`` resolution on a requirement whose step has not yet executed → 400
    - ``ROUTE_SELECT`` resolution on a non-Router requirement → 400
    """
    if resolution in (RequirementResolution.CONFIRM, RequirementResolution.REJECT):
        # confirm / reject are always valid (they're the base agno verbs).
        return

    if resolution == RequirementResolution.EDIT:
        if not requirement.get("requires_output_review"):
            raise HTTPException(
                status_code=400,
                detail="EDIT resolution is only valid for output_review requirements",
            )
        if not requirement.get("is_post_execution"):
            raise HTTPException(
                status_code=400,
                detail="EDIT resolution requires the step to have executed already",
            )
        return

    if resolution == RequirementResolution.USER_INPUT:
        if not requirement.get("requires_user_input"):
            raise HTTPException(
                status_code=400,
                detail="USER_INPUT resolution is only valid for requirements with requires_user_input",
            )
        return

    if resolution == RequirementResolution.ROUTE_SELECT:
        if not requirement.get("requires_route_selection"):
            raise HTTPException(
                status_code=400,
                detail="ROUTE_SELECT resolution is only valid for router route-selection requirements",
            )
        return


def _build_resolution_update(
    resolution: RequirementResolution,
    feedback: str | None,
    edited_output: Any | None,
    user_input: dict[str, Any] | None,
    selected_choices: list[str] | None,
) -> dict[str, Any]:
    """Translate the user's decision into the dict fields agno's StepRequirement consumes.

    Mirrors the methods on ``agno.workflow.types.StepRequirement``
    (confirm / reject / edit / set_user_input / set_selected_choices) but written
    as a dict mutation we can pass straight to MongoDB ``$set``.
    """
    if resolution == RequirementResolution.CONFIRM:
        return {"confirmed": True}

    if resolution == RequirementResolution.REJECT:
        update: dict[str, Any] = {"confirmed": False}
        if feedback is not None:
            update["rejection_feedback"] = feedback
        return update

    if resolution == RequirementResolution.EDIT:
        if edited_output is None:
            raise HTTPException(
                status_code=400,
                detail="EDIT resolution requires non-null edited_output",
            )
        return {"confirmed": True, "edited_output": edited_output}

    if resolution == RequirementResolution.USER_INPUT:
        if user_input is None:
            raise HTTPException(
                status_code=400,
                detail="USER_INPUT resolution requires non-null user_input",
            )
        # confirmed=True signals agno to proceed; user_input is consumed by the executor.
        return {"confirmed": True, "user_input": user_input}

    if resolution == RequirementResolution.ROUTE_SELECT:
        if not selected_choices:
            raise HTTPException(
                status_code=400,
                detail="ROUTE_SELECT resolution requires non-empty selected_choices",
            )
        return {"confirmed": True, "selected_choices": selected_choices}

    raise HTTPException(status_code=400, detail=f"Unknown resolution: {resolution!r}")
