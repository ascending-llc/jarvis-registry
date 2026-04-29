"""
Executor wrapper that injects directive checking and retry backoff into every step.

``with_control`` wraps any StepExecutor and adds:

1. **Directive check** — before each attempt, inspect the in-process
   :class:`~registry_pkgs.workflows.control.queue.DirectiveQueue` (fast path) and
   fall back to reading ``WorkflowRun.pending_directive`` from MongoDB (slow
   path for service-restart / multi-replica scenarios).

2. **Pause handling** — when a PAUSE directive is received, block in a polling
   loop until RESUME or CANCEL arrives, or until ``WorkflowRun.pause_timeout_seconds``
   is exceeded (auto-cancel on timeout).

3. **Exponential-backoff retry** — when ``StepConfig.on_error == "retry"``, retry
   up to ``max_retries`` additional times with ``backoff_base_seconds * 2^attempt``
   wait between attempts, capped at ``backoff_max_seconds``.  Directives are
   checked before every attempt so the run can be paused or cancelled mid-retry.

4. **Attempt persistence** — ``NodeRun.attempt`` is incremented and written to
   MongoDB at the start of each attempt so the UI always reflects the latest try.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime

from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor
from beanie import PydanticObjectId

from registry_pkgs.models.enums import NodeRunStatus, WorkflowDirective, WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, StepConfig, WorkflowRun
from registry_pkgs.workflows.control.queue import DirectiveQueue

logger = logging.getLogger(__name__)

# How often (seconds) the pause-wait loop polls when the Queue times out,
# allowing the MongoDB fallback and timeout check to run.
PAUSE_POLL_INTERVAL: float = 2.0


class WorkflowCancelledError(Exception):
    """Raised when a workflow run is cancelled by a user directive or timeout."""


def with_control(
    executor: StepExecutor,
    *,
    run_id: str,
    node_id: str,
    node_name: str,
    step_config: StepConfig | None,
    directive_queue: DirectiveQueue,
) -> StepExecutor:
    """Wrap *executor* with directive checking and retry-backoff logic.

    Args:
        executor:        The underlying step executor to wrap.
        run_id:          String form of the WorkflowRun ObjectId.
        node_id:         Unique node ID within the workflow definition tree.
        node_name:       Human-readable step name (used in log messages).
        step_config:     Per-step retry / error-handling policy, or ``None`` for
                         the safe production default (no retry, fail-fast).
        directive_queue: The shared in-process DirectiveQueue.

    Returns:
        A new async callable with the same signature as *executor*.
    """
    if step_config and step_config.on_error == "retry":
        max_attempts = 1 + max(step_config.max_retries, 0)
        backoff_base = step_config.backoff_base_seconds
        backoff_cap = step_config.backoff_max_seconds
    else:
        max_attempts = 1
        backoff_base = 1.0
        backoff_cap = 60.0

    async def wrapped(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
        for attempt in range(max_attempts):
            # 1. Check for a pending directive before starting the attempt
            cancel_reason = await _check_and_handle_directive(
                run_id=run_id,
                node_name=node_name,
                directive_queue=directive_queue,
            )
            if cancel_reason is not None:
                raise WorkflowCancelledError(cancel_reason)

            # 2. Record this attempt in NodeRun
            await _record_attempt_start(run_id, node_id, node_name, attempt)

            # 3. Execute the underlying step
            logger.info(
                "[run=%s] ▶ step %r attempt %d/%d — starting",
                run_id,
                node_name,
                attempt + 1,
                max_attempts,
            )
            result: StepOutput = await executor(step_input, session_state)

            if result.success:
                preview = (result.content or "")[:300]
                logger.info(
                    "[run=%s] ✓ step %r — completed  output=%r",
                    run_id,
                    node_name,
                    preview,
                )
                return result

            # 4. Handle failure
            if attempt < max_attempts - 1:
                wait_secs = min(backoff_base * math.pow(2, attempt), backoff_cap)
                logger.info(
                    "Node %r attempt %d/%d failed (%s), retrying in %.1fs",
                    node_name,
                    attempt + 1,
                    max_attempts,
                    result.error,
                    wait_secs,
                )
                await asyncio.sleep(wait_secs)
                continue

            logger.warning("Node %r: all %d attempt(s) failed, last error: %s", node_name, max_attempts, result.error)
            return result

        return StepOutput(content="", success=False, error="Max retries exceeded")

    return wrapped


async def _check_and_handle_directive(
    *,
    run_id: str,
    node_name: str,
    directive_queue: DirectiveQueue,
) -> str | None:
    """Inspect the directive queue and block if paused.

    Returns a human-readable cancellation reason if the run should be
    cancelled (step must not proceed), otherwise ``None``.
    """
    directive = directive_queue.get_nowait(run_id)

    # Queue fast-path missed — fall back to MongoDB for multi-replica / restart resilience.
    if directive is None:
        directive = await _read_mongodb_directive(run_id)

    if directive is None:
        return None

    if directive == WorkflowDirective.CANCEL:
        await _update_run_control_state(run_id, pending_directive=None)
        logger.info("Node %r: CANCEL directive received, aborting step", node_name)
        return "Workflow cancelled by user"

    if directive == WorkflowDirective.PAUSE:
        return await _wait_while_paused(run_id=run_id, node_name=node_name, directive_queue=directive_queue)

    return None


async def _read_mongodb_directive(run_id: str) -> WorkflowDirective | None:
    """Read ``WorkflowRun.pending_directive`` from MongoDB.

    Slow-path fallback used when the Queue is empty.  Covers two scenarios:
    * The service restarted and the Queue was lost.
    * A directive was sent to a different replica and never reached this Queue.
    """
    run = await WorkflowRun.get(PydanticObjectId(run_id))
    return run.pending_directive if run is not None else None


async def _wait_while_paused(
    *,
    run_id: str,
    node_name: str,
    directive_queue: DirectiveQueue,
) -> str | None:
    """Block in a polling loop until RESUME or CANCEL, or until timeout.

    Returns a human-readable cancellation reason if the run was cancelled (or
    timed out), otherwise ``None`` if it was successfully resumed.
    """
    logger.info("Node %r: PAUSE directive received, waiting for RESUME or CANCEL", node_name)

    run = await WorkflowRun.get(PydanticObjectId(run_id))
    await _update_run_control_state(
        run_id,
        status=WorkflowRunStatus.PAUSED,
        pending_directive=None,
        paused_at=datetime.now(UTC),
    )
    if run is None:
        timeout_secs = 3600.0
        paused_at = datetime.now(UTC)
    else:
        timeout_secs = float(run.pause_timeout_seconds)
        paused_at = run.paused_at or datetime.now(UTC)

    while True:
        next_directive = await directive_queue.wait_for_directive(run_id, timeout=PAUSE_POLL_INTERVAL)

        if next_directive is None:
            next_directive = await _read_mongodb_directive(run_id)

        if next_directive == WorkflowDirective.RESUME:
            await _update_run_control_state(
                run_id,
                status=WorkflowRunStatus.RUNNING,
                pending_directive=None,
                paused_at=None,
            )
            logger.info("Node %r: RESUME received, continuing execution", node_name)
            return None

        if next_directive == WorkflowDirective.CANCEL:
            await _update_run_control_state(run_id, pending_directive=None)
            logger.info("Node %r: CANCEL received while paused, aborting", node_name)
            return "Workflow cancelled by user"

        elapsed = (datetime.now(UTC) - paused_at).total_seconds()
        if elapsed >= timeout_secs:
            await _update_run_control_state(run_id, pending_directive=None, paused_at=None)
            logger.warning(
                "Node %r: pause timeout (%.0fs) exceeded, auto-cancelling",
                node_name,
                timeout_secs,
            )
            return f"Workflow cancelled after pause timeout ({int(timeout_secs)}s)"


async def _record_attempt_start(
    run_id: str,
    node_id: str,
    node_name: str,
    attempt: int,
) -> None:
    """Upsert a NodeRun to record that a new attempt is starting.

    Sets ``attempt`` to the current zero-based index and stamps ``started_at``
    on the first attempt only.
    """
    run_oid = PydanticObjectId(run_id)
    node_run = await NodeRun.find_one(
        NodeRun.workflow_run_id == run_oid,
        NodeRun.node_id == node_id,
    )
    if node_run is None:
        node_run = NodeRun(
            workflow_run_id=run_oid,
            node_id=node_id,
            node_name=node_name,
            started_at=datetime.now(UTC),
        )
    node_run.attempt = attempt + 1
    node_run.status = NodeRunStatus.RUNNING
    if attempt == 0:
        node_run.started_at = datetime.now(UTC)
    await node_run.save()
    logger.debug("NodeRun %s (%r) attempt=%d recorded", node_id, node_name, attempt)


async def _update_run_control_state(
    run_id: str,
    *,
    status: WorkflowRunStatus | None = None,
    pending_directive: WorkflowDirective | None = None,
    paused_at: datetime | None = None,
) -> None:
    """Persist control-related WorkflowRun fields without touching unrelated state."""
    run = await WorkflowRun.get(PydanticObjectId(run_id))
    if run is None:
        return
    if status is not None:
        run.status = status
    run.pending_directive = pending_directive
    run.paused_at = paused_at
    await run.save()
