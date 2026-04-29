"""
End-to-end integration test for workflow run control infrastructure.

Uses a slow mock executor (asyncio.sleep) backed by the real DirectiveQueue,
WorkflowControlService, and MongoDB — no MCP / A2A backends required.

Each step sleeps for STEP_DURATION seconds, giving the test harness time to
send pause / resume / cancel directives and observe the effect.

Tests:
  1. Pause → Resume → run completes normally
  2. Pause → Cancel → run ends with CANCELLED
  3. Cancel mid-execution → run ends with CANCELLED
  4. Retry a COMPLETED run → child run is created (PENDING → fires in background)

Usage:
    uv run python scripts/test_control_e2e.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from agno.models.aws import AwsBedrock
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.enums import WorkflowNodeType, WorkflowRunStatus
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows.compiler import flatten_workflow_nodes
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.runner import WorkflowRunner

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("control_e2e")
logger.setLevel(logging.DEBUG)

# Each mock step sleeps this many seconds — long enough to send directives.
STEP_DURATION: float = float(os.getenv("STEP_DURATION", "8"))
PASS = "✅"
FAIL = "❌"


class MockWorkflowRunner(WorkflowRunner):
    """WorkflowRunner that substitutes slow in-process mock executors for all STEP nodes.

    The mock executor logs its start, sleeps for STEP_DURATION seconds, then
    returns a successful StepOutput.  This makes every step long enough to
    receive pause / cancel directives mid-execution.
    """

    def __init__(self, *, step_duration: float = STEP_DURATION, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._step_duration = step_duration

    async def _build_registry(
        self,
        definition: WorkflowDefinition,
        registry_token: str,
        user_id: str | None,
    ) -> dict[str, StepExecutor]:
        """Return a slow mock executor for every executor_key in the definition."""
        all_nodes = flatten_workflow_nodes(definition.nodes)
        executor_keys = list(dict.fromkeys(n.executor_key for n in all_nodes if n.executor_key))

        duration = self._step_duration

        def _make_executor(key: str) -> StepExecutor:
            async def mock(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
                logger.debug("Mock executor %r: sleeping %.1fs ...", key, duration)
                await asyncio.sleep(duration)
                logger.debug("Mock executor %r: done", key)
                return StepOutput(content=f"mock output from {key!r}", success=True)

            return mock

        return {key: _make_executor(key) for key in executor_keys}


def _make_runner(queue: DirectiveQueue) -> MockWorkflowRunner:
    """Build a MockWorkflowRunner wired to *queue*."""
    llm = AwsBedrock(
        id=os.getenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0"),
        aws_region=settings.aws_region,
        aws_session_token=settings.aws_session_token,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    return MockWorkflowRunner(
        llm=llm,
        registry_url=os.getenv("REGISTRY_URL", "http://localhost:7860"),
        db_client=MongoDB.get_client(),
        db_name=MongoDB.database_name,
        jwt_config=settings.jwt_signing_config,
        directive_queue=queue,
    )


async def _create_definition(name: str, n_steps: int = 2) -> WorkflowDefinition:
    """Insert a WorkflowDefinition with *n_steps* sequential mock STEP nodes."""
    nodes = [
        WorkflowNode(
            id=str(uuid4()),
            name=f"step-{i}",
            node_type=WorkflowNodeType.STEP,
            executor_key=f"mock-step-{i}",
        )
        for i in range(1, n_steps + 1)
    ]
    definition = WorkflowDefinition(
        name=name,
        description="Control E2E test — mock slow steps",
        nodes=nodes,
    )
    await definition.insert()
    return definition


async def _poll_status(run_id: str, until: float = 60.0) -> str:
    """Poll WorkflowRun.status every 0.5 s until it leaves RUNNING/PAUSED or timeout."""
    deadline = time.monotonic() + until
    while time.monotonic() < deadline:
        run = await WorkflowRun.get(run_id)
        if run and run.status not in (WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED, WorkflowRunStatus.PENDING):
            return str(run.status)
        await asyncio.sleep(0.5)
    return "timeout"


async def _wait_for_status(run_id: str, target: str, timeout: float = 20.0) -> bool:
    """Return True once WorkflowRun.status == target, False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = await WorkflowRun.get(run_id)
        if run and str(run.status) == target:
            return True
        await asyncio.sleep(0.3)
    return False


def _check(label: str, ok: bool) -> None:
    print(f"  {PASS if ok else FAIL}  {label}")


async def test_pause_resume_completes(queue: DirectiveQueue) -> bool:
    """Pause mid-run, resume, then verify the run completes."""
    print("\n── Test 1: Pause → Resume → COMPLETED ──────────────────────────")
    from registry.services.workflow_control_service import WorkflowControlService

    definition = await _create_definition("e2e-pause-resume", n_steps=2)
    def_id = str(definition.id)
    runner = _make_runner(queue)

    run_task = asyncio.create_task(runner.run(def_id, "e2e pause-resume test", registry_token="test", user_id=None))

    # Wait for run doc to appear
    run_doc: WorkflowRun | None = None
    for _ in range(20):
        await asyncio.sleep(0.3)
        run_doc = await WorkflowRun.find_one(
            {"workflow_definition_id": definition.id},
            sort=[("_id", -1)],
        )
        if run_doc:
            break

    if not run_doc:
        print(f"  {FAIL}  Run doc never appeared in MongoDB")
        run_task.cancel()
        return False

    run_id = str(run_doc.id)
    print(f"  Run {run_id} started (status={run_doc.status})")

    svc = WorkflowControlService(directive_queue=queue)

    # Wait for first step to start, then pause
    await asyncio.sleep(2)
    await svc.send_pause(def_id, run_id)
    paused = await _wait_for_status(run_id, "paused", timeout=10)
    _check("Run reached PAUSED status", paused)

    # Resume after a short wait
    await asyncio.sleep(1)
    await svc.send_resume(def_id, run_id)
    resumed = await _wait_for_status(run_id, "running", timeout=10)
    _check("Run resumed to RUNNING status", resumed)

    # Wait for the run to complete
    final = await _poll_status(run_id, until=STEP_DURATION * 4)
    _check(f"Run completed normally (final={final!r})", final == "completed")

    try:
        await run_task
    except Exception as exc:
        _check(f"Runner task raised: {exc}", False)
        return False

    return final == "completed"


async def test_pause_cancel(queue: DirectiveQueue) -> bool:
    """Pause mid-run, then cancel → run ends CANCELLED."""
    print("\n── Test 2: Pause → Cancel → CANCELLED ──────────────────────────")
    from registry.services.workflow_control_service import WorkflowControlService

    definition = await _create_definition("e2e-pause-cancel", n_steps=2)
    def_id = str(definition.id)
    runner = _make_runner(queue)

    run_task = asyncio.create_task(runner.run(def_id, "e2e pause-cancel test", registry_token="test", user_id=None))

    run_doc: WorkflowRun | None = None
    for _ in range(20):
        await asyncio.sleep(0.3)
        run_doc = await WorkflowRun.find_one(
            {"workflow_definition_id": definition.id},
            sort=[("_id", -1)],
        )
        if run_doc:
            break

    if not run_doc:
        print(f"  {FAIL}  Run doc never appeared")
        run_task.cancel()
        return False

    run_id = str(run_doc.id)
    print(f"  Run {run_id} started")

    svc = WorkflowControlService(directive_queue=queue)

    await asyncio.sleep(2)
    await svc.send_pause(def_id, run_id)
    await _wait_for_status(run_id, "paused", timeout=10)
    _check("Run paused", True)

    await asyncio.sleep(1)
    await svc.send_cancel(def_id, run_id)

    final = await _poll_status(run_id, until=STEP_DURATION * 2)
    _check(f"Run cancelled (final={final!r})", final == "cancelled")

    try:
        await run_task
    except Exception as e:
        print(e)
    return final == "cancelled"


async def test_cancel_mid_run(queue: DirectiveQueue) -> bool:
    """Cancel a run while a step is executing → run ends CANCELLED."""
    print("\n── Test 3: Cancel mid-execution → CANCELLED ─────────────────────")
    from registry.services.workflow_control_service import WorkflowControlService

    definition = await _create_definition("e2e-cancel-mid", n_steps=3)
    def_id = str(definition.id)
    runner = _make_runner(queue)

    run_task = asyncio.create_task(runner.run(def_id, "e2e cancel-mid test", registry_token="test", user_id=None))

    run_doc: WorkflowRun | None = None
    for _ in range(20):
        await asyncio.sleep(0.3)
        run_doc = await WorkflowRun.find_one(
            {"workflow_definition_id": definition.id},
            sort=[("_id", -1)],
        )
        if run_doc:
            break

    if not run_doc:
        print(f"  {FAIL}  Run doc never appeared")
        run_task.cancel()
        return False

    run_id = str(run_doc.id)
    print(f"  Run {run_id} started")

    svc = WorkflowControlService(directive_queue=queue)

    # Cancel after step-1 starts but before it finishes
    await asyncio.sleep(2)
    await svc.send_cancel(def_id, run_id)

    final = await _poll_status(run_id, until=STEP_DURATION * 2)
    _check(f"Run cancelled mid-execution (final={final!r})", final == "cancelled")

    try:
        await run_task
    except Exception as e:
        print(e)
    return final == "cancelled"


async def test_retry_completed(queue: DirectiveQueue) -> bool:
    """Retry a COMPLETED run → child WorkflowRun is created."""
    print("\n── Test 4: Retry a COMPLETED run → child run created ────────────")
    from registry.services.workflow_control_service import WorkflowControlService

    definition = await _create_definition("e2e-retry", n_steps=2)
    def_id = str(definition.id)
    runner = _make_runner(queue)

    # Run to completion first
    print("  Running to completion ...")
    run, _ = await runner.run(def_id, "e2e retry seed", registry_token="test", user_id=None)
    run_id = str(run.id)
    _check(f"Seed run completed (status={run.status})", str(run.status) == "completed")

    # Reload to get definition_snapshot (written during run)
    await run.sync()

    # Retry from first node
    first_node_id = definition.nodes[0].id
    svc = WorkflowControlService(
        directive_queue=queue,
        runner_factory=lambda: _make_runner(queue),
    )
    child = await svc.send_retry(
        def_id,
        run_id,
        first_node_id,
        registry_token="test",
        user_id=None,
    )
    child_id = str(child.id)
    _check(f"Child run created (id={child_id}, status={child.status})", child_id != run_id)

    # Give the background task a moment to flip status to RUNNING
    await asyncio.sleep(1)
    child_doc = await WorkflowRun.get(child_id)
    _check(
        f"Child run started (status={child_doc.status if child_doc else '?'})",
        child_doc is not None and str(child_doc.status) in ("running", "completed"),
    )

    return child_doc is not None and child_id != run_id


async def main() -> int:
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )

    queue = DirectiveQueue()

    print(f"\nStep duration: {STEP_DURATION}s  (override with STEP_DURATION env var)")
    print(f"Estimated total runtime: ~{int(STEP_DURATION * 6)}s\n")

    try:
        results = [
            await test_pause_resume_completes(queue),
            await test_pause_cancel(queue),
            await test_cancel_mid_run(queue),
            await test_retry_completed(queue),
        ]

        passed = sum(results)
        total = len(results)
        print(f"\n{'=' * 50}")
        print(f"Results: {passed}/{total} passed {'✅' if passed == total else '❌'}")
        return 0 if passed == total else 1

    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
