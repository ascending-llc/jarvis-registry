import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.database.leased_job import SNAKE_CASE_LEASE_FIELDS, Lease, LeaseLostError
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.workflows import run_lease
from registry_pkgs.workflows.run_lease import (
    LeasedRunStateWriter,
    execute_leased_run,
    fail_open_node_runs,
    reap_expired_runs,
)

_TOKEN = "tok-abc"
_REASON = "Interrupted by registry shutdown; retry the run"


def _lease(doc_id: PydanticObjectId | None = None) -> Lease:
    return Lease(
        doc_id=doc_id or PydanticObjectId(),
        owner="registry-1",
        token=_TOKEN,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
    )


def _patch_run_collection(monkeypatch: pytest.MonkeyPatch, matched_count: int = 1) -> AsyncMock:
    collection = AsyncMock()
    collection.update_one.return_value = SimpleNamespace(matched_count=matched_count, modified_count=matched_count)
    monkeypatch.setattr(run_lease.WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: collection))
    return collection


# ----------------------------- LeasedRunStateWriter --------------------------------


@pytest.mark.asyncio
async def test_leased_writer_fences_every_write_on_token(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_run_collection(monkeypatch)
    writer = LeasedRunStateWriter(_lease())

    await writer.write({"final_output": {"content": "ok"}}, from_statuses={WorkflowRunStatus.RUNNING})

    flt, update = collection.update_one.await_args.args
    assert flt[SNAKE_CASE_LEASE_FIELDS.token] == _TOKEN
    # A non-exit status write leaves the lease fields alone (the run keeps its lease).
    assert SNAKE_CASE_LEASE_FIELDS.owner not in update["$set"]


@pytest.mark.asyncio
async def test_leased_writer_keeps_lease_while_running(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_run_collection(monkeypatch)
    writer = LeasedRunStateWriter(_lease())

    await writer.write({"status": WorkflowRunStatus.RUNNING}, from_statuses={WorkflowRunStatus.PENDING})

    _flt, update = collection.update_one.await_args.args
    assert SNAKE_CASE_LEASE_FIELDS.token not in update["$set"]


@pytest.mark.parametrize(
    "status",
    [WorkflowRunStatus.AWAITING_APPROVAL, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED],
)
@pytest.mark.asyncio
async def test_leased_writer_clears_lease_on_exit_status(
    monkeypatch: pytest.MonkeyPatch, status: WorkflowRunStatus
) -> None:
    collection = _patch_run_collection(monkeypatch)
    writer = LeasedRunStateWriter(_lease())

    await writer.write({"status": status}, from_statuses={WorkflowRunStatus.RUNNING})

    _flt, update = collection.update_one.await_args.args
    assert update["$set"][SNAKE_CASE_LEASE_FIELDS.owner] is None
    assert update["$set"][SNAKE_CASE_LEASE_FIELDS.token] is None
    assert update["$set"][SNAKE_CASE_LEASE_FIELDS.expires_at] is None


# ----------------------------- fail_open_node_runs ---------------------------------


@pytest.mark.asyncio
async def test_fail_open_node_runs_updates_open_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = AsyncMock()
    collection.update_many.return_value = SimpleNamespace(modified_count=3)
    monkeypatch.setattr(run_lease.NodeRun, "get_pymongo_collection", classmethod(lambda cls: collection))
    run_id = PydanticObjectId()

    count = await fail_open_node_runs(run_id, "boom")

    assert count == 3
    flt, update = collection.update_many.await_args.args
    assert flt["workflow_run_id"] == run_id
    assert set(flt["status"]["$in"]) == {"pending", "running", "awaiting_approval"}
    assert update["$set"]["status"] == "failed"
    assert update["$set"]["error"] == "boom"


# ----------------------------- execute_leased_run ----------------------------------


@pytest.mark.asyncio
async def test_execute_leased_run_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_with_lease(repo, lease, coro, *, on_cancelled=None):
        return await coro

    monkeypatch.setattr(run_lease, "run_with_lease", fake_run_with_lease)

    async def coro():
        return "done"

    assert await execute_leased_run(_lease(), coro(), interrupted_reason=_REASON) == "done"


@pytest.mark.asyncio
async def test_execute_leased_run_lease_lost_self_release_is_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = _lease()

    async def fake_run_with_lease(repo, lease, coro, *, on_cancelled=None):
        coro.close()
        raise LeaseLostError("gone")

    monkeypatch.setattr(run_lease, "run_with_lease", fake_run_with_lease)
    # Run is now AWAITING_APPROVAL → it released its own lease; write nothing.
    monkeypatch.setattr(
        run_lease.WorkflowRun,
        "get",
        AsyncMock(return_value=SimpleNamespace(status=WorkflowRunStatus.AWAITING_APPROVAL)),
    )
    transition = AsyncMock()
    monkeypatch.setattr(run_lease.workflow_run_lease_repository, "transition", transition)

    async def coro():
        return "x"

    assert await execute_leased_run(lease, coro(), interrupted_reason=_REASON) is None
    transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_leased_run_other_exception_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_with_lease(repo, lease, coro, *, on_cancelled=None):
        coro.close()
        raise ValueError("WorkflowDefinition not found")

    monkeypatch.setattr(run_lease, "run_with_lease", fake_run_with_lease)
    transition = AsyncMock()
    monkeypatch.setattr(run_lease.workflow_run_lease_repository, "transition", transition)

    async def coro():
        return "x"

    with pytest.raises(ValueError):
        await execute_leased_run(_lease(), coro(), interrupted_reason=_REASON)

    set_fields = transition.await_args.args[1]
    assert set_fields["status"] == WorkflowRunStatus.FAILED.value
    assert "Execution error:" in set_fields["error_summary"]
    assert transition.await_args.kwargs["release"] is True


@pytest.mark.asyncio
async def test_execute_leased_run_cancel_finalizes_then_fails_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = _lease()

    async def fake_run_with_lease(repo, lease, coro, *, on_cancelled=None):
        coro.close()
        await on_cancelled()  # runs first, while the lease is still held
        raise asyncio.CancelledError

    monkeypatch.setattr(run_lease, "run_with_lease", fake_run_with_lease)
    transition = AsyncMock(return_value=True)  # the FAILED+release write matched
    monkeypatch.setattr(run_lease.workflow_run_lease_repository, "transition", transition)
    fail_nodes = AsyncMock(return_value=2)
    monkeypatch.setattr(run_lease, "fail_open_node_runs", fail_nodes)

    async def coro():
        return "x"

    with pytest.raises(asyncio.CancelledError):
        await execute_leased_run(lease, coro(), interrupted_reason=_REASON)

    set_fields = transition.await_args.args[1]
    assert set_fields["status"] == WorkflowRunStatus.FAILED.value
    assert set_fields["error_summary"] == _REASON
    # NodeRuns are only failed AFTER the execution is cancelled (so the wrapper can't reopen them).
    fail_nodes.assert_awaited_once_with(lease.doc_id, _REASON)


@pytest.mark.asyncio
async def test_execute_leased_run_cancel_without_match_skips_node_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_with_lease(repo, lease, coro, *, on_cancelled=None):
        coro.close()
        await on_cancelled()
        raise asyncio.CancelledError

    monkeypatch.setattr(run_lease, "run_with_lease", fake_run_with_lease)
    monkeypatch.setattr(
        run_lease.workflow_run_lease_repository, "transition", AsyncMock(return_value=False)
    )  # already terminal, write did not match
    fail_nodes = AsyncMock()
    monkeypatch.setattr(run_lease, "fail_open_node_runs", fail_nodes)

    async def coro():
        return "x"

    with pytest.raises(asyncio.CancelledError):
        await execute_leased_run(_lease(), coro(), interrupted_reason=_REASON)

    fail_nodes.assert_not_awaited()


# ----------------------------- reap_expired_runs -----------------------------------


@pytest.mark.asyncio
async def test_reap_pass1_fails_expired_lease_and_its_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    reaped_run = SimpleNamespace(id=PydanticObjectId())
    reap_one = AsyncMock(side_effect=[reaped_run, None])
    monkeypatch.setattr(run_lease.workflow_run_lease_repository, "reap_one", reap_one)
    # No legacy runs in pass 2.
    legacy_collection = AsyncMock()
    legacy_collection.find_one_and_update.return_value = None
    monkeypatch.setattr(run_lease.WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: legacy_collection))
    fail_nodes = AsyncMock(return_value=1)
    monkeypatch.setattr(run_lease, "fail_open_node_runs", fail_nodes)

    count = await reap_expired_runs(legacy_cutover_age=timedelta(seconds=300))

    assert count == 1
    set_fields = reap_one.await_args_list[0].kwargs["set_fields"]
    assert set_fields["status"] == WorkflowRunStatus.FAILED.value
    fail_nodes.assert_awaited_once_with(reaped_run.id, "Executor lost: lease expired")


@pytest.mark.asyncio
async def test_reap_pass2_fails_legacy_leaseless_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_lease.workflow_run_lease_repository, "reap_one", AsyncMock(return_value=None))
    legacy_id = PydanticObjectId()
    collection = AsyncMock()
    collection.find_one_and_update.side_effect = [{"_id": legacy_id}, None]
    monkeypatch.setattr(run_lease.WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: collection))
    fail_nodes = AsyncMock(return_value=0)
    monkeypatch.setattr(run_lease, "fail_open_node_runs", fail_nodes)

    count = await reap_expired_runs(legacy_cutover_age=timedelta(seconds=300))

    assert count == 1
    flt, _update = collection.find_one_and_update.await_args_list[0].args
    assert flt[SNAKE_CASE_LEASE_FIELDS.token] == {"$exists": False}
    assert set(flt["status"]["$in"]) == {"pending", "running", "paused"}
    fail_nodes.assert_awaited_once_with(legacy_id, "Executor lost: run predates run leasing")


# ----------------------------- fence / heartbeat / save guard -----------------------


@pytest.mark.asyncio
async def test_leased_writer_foreign_token_write_no_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    """A write whose lease token no longer matches the document (reaped / re-claimed by another
    owner) matches zero documents and returns False — the old owner cannot modify the run."""
    collection = _patch_run_collection(monkeypatch, matched_count=0)
    writer = LeasedRunStateWriter(_lease())

    applied = await writer.write({"status": WorkflowRunStatus.COMPLETED}, from_statuses={WorkflowRunStatus.RUNNING})

    assert applied is False
    flt, _update = collection.update_one.await_args.args
    assert flt[SNAKE_CASE_LEASE_FIELDS.token] == _TOKEN  # the filter is fenced on our token


@pytest.mark.asyncio
async def test_workflow_run_lease_heartbeat_renews_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A RUNNING/PAUSED leased run keeps its lease alive: heartbeat pushes lease_expires_at forward."""
    collection = _patch_run_collection(monkeypatch, matched_count=1)
    lease = _lease()
    original_expiry = lease.expires_at

    renewed = await run_lease.workflow_run_lease_repository.heartbeat(lease)

    assert renewed is not None
    assert renewed.expires_at > original_expiry
    _flt, update = collection.update_one.await_args.args
    assert SNAKE_CASE_LEASE_FIELDS.expires_at in update["$set"]


@pytest.mark.asyncio
async def test_execute_leased_run_exception_is_noop_when_already_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the runner already wrote a terminal status, the exception-branch FAILED write is fenced
    out (transition returns False); execute_leased_run changes nothing and still re-raises."""

    async def fake_run_with_lease(repo, lease, coro, *, on_cancelled=None):
        coro.close()
        raise RuntimeError("late failure after terminal")

    monkeypatch.setattr(run_lease, "run_with_lease", fake_run_with_lease)
    # transition rejected because the run already left the leased state (first-terminal-wins).
    transition = AsyncMock(return_value=False)
    monkeypatch.setattr(run_lease.workflow_run_lease_repository, "transition", transition)

    async def coro():
        return "x"

    with pytest.raises(RuntimeError, match="late failure"):
        await execute_leased_run(_lease(), coro(), interrupted_reason=_REASON)

    transition.assert_awaited_once()  # attempted, but no-op (returned False); nothing else written


def test_workflow_run_save_is_blocked_by_lease_mixin() -> None:
    """WorkflowRun is lease-fenced: a stray whole-document save() must raise, forcing writes through
    the repository/writer so the lease fields are never silently reset."""
    run = run_lease.WorkflowRun.model_construct(id=PydanticObjectId())
    with pytest.raises(TypeError):
        run.save()
