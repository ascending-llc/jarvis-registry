import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from beanie import PydanticObjectId

from registry.services import workflow_run_launcher as launcher_module
from registry.services.background_task_tracker import BackgroundTaskTracker
from registry.services.workflow_run_launcher import (
    _REGISTRY_SHUTDOWN_REASON,
    ABORT_CONTINUE,
    WorkflowRunLauncher,
)
from registry_pkgs.database.leased_job import Lease
from registry_pkgs.models.enums import WorkflowRunStatus


def _lease(doc_id: PydanticObjectId) -> Lease:
    return Lease(doc_id=doc_id, owner="registry-1", token="tok", expires_at=datetime.now(UTC) + timedelta(minutes=2))


async def _drain(tracker: BackgroundTaskTracker) -> None:
    await asyncio.gather(*list(tracker._tasks), return_exceptions=True)


@pytest.fixture
def runner() -> SimpleNamespace:
    # run/continue_run return plain sentinels (not coroutines) since execute_leased_run is mocked.
    return SimpleNamespace(run=Mock(return_value="RUN_CORO"), continue_run=Mock(return_value="CONT_CORO"))


@pytest.mark.asyncio
async def test_launch_run_spawns_leased_tracked_execution(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    exec_mock = AsyncMock()
    monkeypatch.setattr(launcher_module, "execute_leased_run", exec_mock)
    tracker = BackgroundTaskTracker()
    launcher = WorkflowRunLauncher(runner, "registry-1", tracker)
    run_id = PydanticObjectId()
    lease = _lease(run_id)

    await launcher.launch_run(
        lease=lease,
        definition_id="def-1",
        user_text="hello",
        auth_context=None,
        existing_run_id=str(run_id),
    )
    await _drain(tracker)

    runner.run.assert_called_once()
    assert runner.run.call_args.kwargs["existing_run_id"] == str(run_id)
    assert exec_mock.await_args.args[0] is lease
    assert exec_mock.await_args.args[1] == "RUN_CORO"
    assert exec_mock.await_args.kwargs["interrupted_reason"] == _REGISTRY_SHUTDOWN_REASON


@pytest.mark.asyncio
async def test_launch_run_after_shutdown_fails_the_run_and_raises(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    transition = AsyncMock()
    monkeypatch.setattr(launcher_module.workflow_run_lease_repository, "transition", transition)
    tracker = BackgroundTaskTracker()
    await tracker.shutdown()  # tracker now refuses spawns
    launcher = WorkflowRunLauncher(runner, "registry-1", tracker)
    run_id = PydanticObjectId()
    lease = _lease(run_id)

    with pytest.raises(RuntimeError):
        await launcher.launch_run(
            lease=lease, definition_id="def-1", user_text="x", auth_context=None, existing_run_id=str(run_id)
        )

    # The already-inserted run is failed with a cleared lease, not left PENDING for the reaper.
    set_fields = transition.await_args.args[1]
    assert set_fields["status"] == WorkflowRunStatus.FAILED.value
    assert set_fields["error_summary"] == _REGISTRY_SHUTDOWN_REASON
    assert transition.await_args.kwargs["release"] is True


@pytest.mark.asyncio
async def test_launch_continue_acquires_and_runs(runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = PydanticObjectId()
    acquire = AsyncMock(return_value=_lease(run_id))
    monkeypatch.setattr(launcher_module.workflow_run_lease_repository, "acquire", acquire)
    exec_mock = AsyncMock()
    monkeypatch.setattr(launcher_module, "execute_leased_run", exec_mock)
    tracker = BackgroundTaskTracker()
    launcher = WorkflowRunLauncher(runner, "registry-1", tracker)

    async def prepare() -> None:
        return None  # a legitimate "no auth context" continue (not an abort)

    await launcher.launch_continue(run_id=run_id, prepare=prepare)
    await _drain(tracker)

    assert acquire.await_args.kwargs["expected_filter"] == {"status": WorkflowRunStatus.AWAITING_APPROVAL.value}
    assert acquire.await_args.kwargs["set_fields"] == {"status": WorkflowRunStatus.RUNNING.value}
    runner.continue_run.assert_called_once()
    assert exec_mock.await_args.args[1] == "CONT_CORO"


@pytest.mark.asyncio
async def test_launch_continue_race_lost_never_runs(runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher_module.workflow_run_lease_repository, "acquire", AsyncMock(return_value=None))
    exec_mock = AsyncMock()
    monkeypatch.setattr(launcher_module, "execute_leased_run", exec_mock)
    tracker = BackgroundTaskTracker()
    launcher = WorkflowRunLauncher(runner, "registry-1", tracker)

    async def prepare() -> None:
        return None

    await launcher.launch_continue(run_id=PydanticObjectId(), prepare=prepare)
    await _drain(tracker)

    runner.continue_run.assert_not_called()
    exec_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_launch_continue_abort_skips_acquire(runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    acquire = AsyncMock()
    monkeypatch.setattr(launcher_module.workflow_run_lease_repository, "acquire", acquire)
    tracker = BackgroundTaskTracker()
    launcher = WorkflowRunLauncher(runner, "registry-1", tracker)

    async def prepare() -> object:
        return ABORT_CONTINUE

    await launcher.launch_continue(run_id=PydanticObjectId(), prepare=prepare)
    await _drain(tracker)

    acquire.assert_not_awaited()
    runner.continue_run.assert_not_called()
