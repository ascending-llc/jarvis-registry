from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.workflows import run_repository
from registry_pkgs.workflows.run_repository import NON_TERMINAL_RUN_STATUSES, RunStateWriter


def _patch_update(monkeypatch: pytest.MonkeyPatch, matched_count: int, modified_count: int | None = None) -> AsyncMock:
    collection = AsyncMock()
    collection.update_one.return_value = SimpleNamespace(
        matched_count=matched_count,
        modified_count=matched_count if modified_count is None else modified_count,
    )
    monkeypatch.setattr(run_repository.WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: collection))
    return collection


@pytest.mark.asyncio
async def test_write_rejects_control_plane_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_update(monkeypatch, 1)
    writer = RunStateWriter(PydanticObjectId())

    with pytest.raises(ValueError, match="pending_directive"):
        await writer.write({"pending_directive": "cancel"}, from_statuses={WorkflowRunStatus.RUNNING})


@pytest.mark.asyncio
async def test_ack_directive_is_the_only_path_that_clears_pending_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _patch_update(monkeypatch, 1)
    writer = RunStateWriter(PydanticObjectId())

    await writer.ack_directive("pause", {"status": WorkflowRunStatus.PAUSED}, from_statuses={WorkflowRunStatus.RUNNING})

    _flt, update = collection.update_one.await_args.args
    assert update["$set"]["pending_directive"] is None


@pytest.mark.asyncio
async def test_write_sets_only_the_given_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    writer = RunStateWriter(PydanticObjectId())

    ok = await writer.write({"final_output": {"content": "done"}}, from_statuses={WorkflowRunStatus.RUNNING})

    assert ok is True
    flt, update = collection.update_one.await_args.args
    # Only final_output is written — nothing else, and in particular NOT the
    # control-plane-owned pending_directive, so a concurrent CANCEL survives.
    assert set(update["$set"].keys()) == {"final_output"}
    assert "pending_directive" not in update["$set"]
    assert "status" not in update["$set"]


@pytest.mark.asyncio
async def test_write_carries_status_guard_and_no_upsert(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    run_id = PydanticObjectId()
    writer = RunStateWriter(run_id)

    await writer.write(
        {"status": WorkflowRunStatus.FAILED, "error_summary": "boom"},
        from_statuses=NON_TERMINAL_RUN_STATUSES,
    )

    flt, _update = collection.update_one.await_args.args
    assert flt["_id"] == run_id
    assert set(flt["status"]["$in"]) == {s.value for s in NON_TERMINAL_RUN_STATUSES}
    # upsert must never be enabled: a stale write must not resurrect a deleted run.
    assert "upsert" not in collection.update_one.await_args.kwargs


@pytest.mark.asyncio
async def test_write_serialises_enum_values(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    writer = RunStateWriter(PydanticObjectId())

    await writer.write({"status": WorkflowRunStatus.COMPLETED}, from_statuses={WorkflowRunStatus.RUNNING})

    _flt, update = collection.update_one.await_args.args
    assert update["$set"]["status"] == "completed"  # raw string, as Beanie stores a StrEnum


@pytest.mark.asyncio
async def test_unset_moves_fields_to_dollar_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    writer = RunStateWriter(PydanticObjectId())

    await writer.write(
        {"status": WorkflowRunStatus.RUNNING}, from_statuses={WorkflowRunStatus.PAUSED}, unset=["finished_at"]
    )

    _flt, update = collection.update_one.await_args.args
    assert update["$unset"] == {"finished_at": ""}


@pytest.mark.asyncio
async def test_write_returns_false_when_guard_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_update(monkeypatch, 0)  # matched_count == 0 -> run left the expected state
    writer = RunStateWriter(PydanticObjectId())

    ok = await writer.write({"status": WorkflowRunStatus.RUNNING}, from_statuses={WorkflowRunStatus.PENDING})

    assert ok is False


@pytest.mark.asyncio
async def test_write_succeeds_on_matched_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_update(monkeypatch, matched_count=1, modified_count=0)
    writer = RunStateWriter(PydanticObjectId())

    ok = await writer.write({"status": WorkflowRunStatus.RUNNING}, from_statuses={WorkflowRunStatus.RUNNING})

    assert ok is True


@pytest.mark.asyncio
async def test_ack_directive_filters_on_consumed_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    writer = RunStateWriter(PydanticObjectId())

    await writer.ack_directive("cancel", from_statuses={WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED})

    flt, _update = collection.update_one.await_args.args
    # CAS on the exact directive consumed: a newer directive written underneath is not erased.
    assert flt["pending_directive"] == "cancel"


@pytest.mark.asyncio
async def test_targeted_write_preserves_concurrently_written_cancel(fake_run_collection) -> None:
    run_id = PydanticObjectId()
    # The control plane wrote CANCEL after the executor loaded its stale copy.
    collection = fake_run_collection({"_id": run_id, "status": "running", "pending_directive": "cancel"})

    # The executor updates only its own field — the old save() would have stamped
    # pending_directive=None over the whole doc and lost the cancel.
    await RunStateWriter(run_id).write({"final_output": {"content": "x"}}, from_statuses={WorkflowRunStatus.RUNNING})

    assert collection.doc["pending_directive"] == "cancel"
    assert collection.doc["final_output"] == {"content": "x"}


@pytest.mark.asyncio
async def test_terminal_write_is_rejected_once_run_is_terminal(fake_run_collection) -> None:
    run_id = PydanticObjectId()
    collection = fake_run_collection({"_id": run_id, "status": "completed"})

    # A late failure finalizer must not overwrite a run that already completed.
    ok = await RunStateWriter(run_id).write(
        {"status": WorkflowRunStatus.FAILED}, from_statuses=NON_TERMINAL_RUN_STATUSES
    )

    assert ok is False
    assert collection.doc["status"] == "completed"
