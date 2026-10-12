"""Real-Mongo verification that registry shutdown finalizes only this pod's leased workflow runs.

Drives the real ``WorkflowRunLauncher`` → ``BackgroundTaskTracker`` → ``execute_leased_run`` path with a
stub runner, so the lease fence, the ``on_cancelled`` finalizer and ``fail_open_node_runs`` all run
against MongoDB itself. Gated on ``MONGO_INTEGRATION_URI``, like the lease library's own tests.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from agno.run.base import RunStatus
from agno.run.workflow import WorkflowRunOutput
from beanie import PydanticObjectId, init_beanie
from pymongo import AsyncMongoClient

from registry.services.background_task_tracker import BackgroundTaskTracker
from registry.services.workflow_run_launcher import _REGISTRY_SHUTDOWN_REASON, WorkflowRunLauncher
from registry_pkgs.models.enums import NodeRunStatus, WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowRun
from registry_pkgs.workflows.persistence import WorkflowRunSyncer
from registry_pkgs.workflows.run_lease import workflow_run_lease_repository
from registry_pkgs.workflows.run_repository import RunStateWriter

pytestmark = pytest.mark.asyncio

_OWNER = "registry-this-pod"


@pytest_asyncio.fixture
async def workflow_db():
    """Init Beanie against a throwaway database so WorkflowRun/NodeRun use real collections."""
    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI to run real-Mongo workflow shutdown tests")
    database_name = f"workflow_shutdown_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    await init_beanie(database=client[database_name], document_models=[WorkflowRun, NodeRun])
    try:
        yield client, database_name
    finally:
        await client.drop_database(database_name)
        await client.close()


class _BlockingRunner:
    """Stub ``WorkflowRunner``: claims the run, opens NodeRuns, then blocks until cancelled.

    On cancellation it starts a detached task, as agno does for its cancelled-run persist, which waits
    for ``release_detached`` and then pushes agno's ``cancelled`` status through the real syncer.
    """

    def __init__(self, db_client: Any, db_name: str) -> None:
        self._db_client = db_client
        self._db_name = db_name
        self.started = asyncio.Event()
        self.release_detached = asyncio.Event()
        self.detached: asyncio.Task[bool] | None = None

    async def run(self, definition_id: str, user_text: str, *, existing_run_id: str, run_writer: RunStateWriter, **_):
        run_id = PydanticObjectId(existing_run_id)
        assert await run_writer.write(
            {"status": WorkflowRunStatus.RUNNING}, from_statuses={WorkflowRunStatus.PENDING}
        ), "start compare-and-set must land while this pod holds the lease"
        await _insert_node_runs(run_id)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            run = await WorkflowRun.get(run_id)
            syncer = WorkflowRunSyncer(run, {}, self._db_client, self._db_name, run_writer=run_writer)
            self.detached = asyncio.create_task(self._persist_cancelled(syncer))
            raise

    async def _persist_cancelled(self, syncer: WorkflowRunSyncer) -> bool:
        await self.release_detached.wait()
        return await syncer._update_workflow_run(WorkflowRunOutput(status=RunStatus.cancelled), [])


async def _insert_node_runs(run_id: PydanticObjectId) -> None:
    for node_id, status in (
        ("running", NodeRunStatus.RUNNING),
        ("gated", NodeRunStatus.AWAITING_APPROVAL),
        ("done", NodeRunStatus.COMPLETED),
    ):
        await NodeRun(workflow_run_id=run_id, node_id=node_id, node_name=node_id, status=status).insert()


async def _node_statuses(run_id: PydanticObjectId) -> dict[str, str]:
    return {
        doc["node_id"]: doc["status"]
        async for doc in NodeRun.get_pymongo_collection().find({"workflow_run_id": run_id})
    }


async def _launch_blocking_run(launcher: WorkflowRunLauncher, runner: _BlockingRunner) -> PydanticObjectId:
    """Insert an already-leased PENDING run, as the trigger route does, and launch it."""
    run_id = PydanticObjectId()
    lease = launcher.new_lease(run_id)
    await WorkflowRun(
        id=run_id,
        workflow_definition_id=PydanticObjectId(),
        status=WorkflowRunStatus.PENDING,
        **workflow_run_lease_repository.insert_fields(lease),
    ).insert()
    await launcher.launch_run(
        lease=lease,
        definition_id=str(PydanticObjectId()),
        user_text="hi",
        auth_context=None,
        existing_run_id=str(run_id),
    )
    await asyncio.wait_for(runner.started.wait(), timeout=5)
    return run_id


async def test_shutdown_fails_launched_run_and_its_open_node_runs(workflow_db):
    client, db_name = workflow_db
    runner = _BlockingRunner(client, db_name)
    tracker = BackgroundTaskTracker()
    launcher = WorkflowRunLauncher(runner, _OWNER, tracker)  # type: ignore[arg-type]
    run_id = await _launch_blocking_run(launcher, runner)

    still_pending = await tracker.shutdown(budget=timedelta(seconds=5))

    assert still_pending == 0
    doc = await WorkflowRun.get_pymongo_collection().find_one({"_id": run_id})
    assert doc["status"] == WorkflowRunStatus.FAILED.value
    assert doc["error_summary"] == _REGISTRY_SHUTDOWN_REASON
    assert doc["finished_at"] is not None
    assert doc["lease_token"] is None and doc["lease_owner"] is None and doc["lease_expires_at"] is None
    assert await _node_statuses(run_id) == {
        "running": NodeRunStatus.FAILED.value,
        "gated": NodeRunStatus.FAILED.value,
        "done": NodeRunStatus.COMPLETED.value,
    }

    runner.release_detached.set()
    assert runner.detached is not None
    await runner.detached


async def test_detached_cancelled_persist_after_shutdown_is_rejected(workflow_db):
    """agno's detached cancelled-run persist, issued as soon as the execution is cancelled, is fenced out.

    The persist is released up front, so it races shutdown's finalization. It is rejected only because
    ``on_cancelled`` writes FAILED and releases the lease *before* the execution is cancelled.
    """
    client, db_name = workflow_db
    runner = _BlockingRunner(client, db_name)
    runner.release_detached.set()
    tracker = BackgroundTaskTracker()
    launcher = WorkflowRunLauncher(runner, _OWNER, tracker)  # type: ignore[arg-type]
    run_id = await _launch_blocking_run(launcher, runner)

    await tracker.shutdown(budget=timedelta(seconds=5))
    assert runner.detached is not None
    written = await asyncio.wait_for(runner.detached, timeout=5)

    assert written is False
    doc = await WorkflowRun.get_pymongo_collection().find_one({"_id": run_id})
    assert doc["status"] == WorkflowRunStatus.FAILED.value  # not overwritten with CANCELLED
    assert doc["error_summary"] == _REGISTRY_SHUTDOWN_REASON


async def test_shutdown_leaves_another_pods_running_run_untouched(workflow_db):
    """The regression the old cluster-wide sweep had: shutdown must touch only this pod's tracked runs."""
    client, db_name = workflow_db
    runs = WorkflowRun.get_pymongo_collection()
    foreign_id = PydanticObjectId()
    foreign_expiry = datetime.now(UTC) + timedelta(minutes=2)
    await runs.insert_one(
        {
            "_id": foreign_id,
            "workflow_definition_id": PydanticObjectId(),
            "status": WorkflowRunStatus.RUNNING.value,
            "started_at": datetime.now(UTC),
            "lease_owner": "registry-other-pod",
            "lease_token": "foreign-token",
            "lease_expires_at": foreign_expiry,
        }
    )
    await _insert_node_runs(foreign_id)
    runner = _BlockingRunner(client, db_name)
    tracker = BackgroundTaskTracker()
    launcher = WorkflowRunLauncher(runner, _OWNER, tracker)  # type: ignore[arg-type]
    own_id = await _launch_blocking_run(launcher, runner)

    await tracker.shutdown(budget=timedelta(seconds=5))

    assert (await runs.find_one({"_id": own_id}))["status"] == WorkflowRunStatus.FAILED.value
    foreign = await runs.find_one({"_id": foreign_id})
    assert foreign["status"] == WorkflowRunStatus.RUNNING.value
    assert foreign["lease_token"] == "foreign-token"
    assert foreign["lease_owner"] == "registry-other-pod"
    assert foreign.get("error_summary") is None
    assert await _node_statuses(foreign_id) == {
        "running": NodeRunStatus.RUNNING.value,
        "gated": NodeRunStatus.AWAITING_APPROVAL.value,
        "done": NodeRunStatus.COMPLETED.value,
    }

    runner.release_detached.set()
    assert runner.detached is not None
    await runner.detached
