import asyncio
from datetime import UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from workflow_worker import executor


@pytest.mark.asyncio
async def test_run_bounded_renews_lease_while_executing(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    runner = SimpleNamespace()
    repository = object()
    execute = AsyncMock()
    renew = AsyncMock()
    monkeypatch.setattr(executor, "_execute_schedule", execute)
    monkeypatch.setattr(executor, "_renew_lease", renew)
    semaphore = asyncio.Semaphore(1)

    await executor.run_bounded(schedule, semaphore, runner, repository, lease_seconds=120)

    execute.assert_awaited_once_with(schedule, runner, repository)
    renew.assert_awaited_once()
    assert renew.await_args.args[:4] == (repository, schedule.id, "lease-1", 120)
    assert semaphore._value == 1


@pytest.mark.asyncio
async def test_run_bounded_rejects_claim_without_lease_token(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(executor, "_execute_schedule", execute)

    await executor.run_bounded(
        SimpleNamespace(id=PydanticObjectId(), lease_token=None),
        asyncio.Semaphore(1),
        SimpleNamespace(),
        object(),
    )

    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_schedule_finishes_only_the_claimed_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    definition = SimpleNamespace(id=PydanticObjectId())
    run = SimpleNamespace(id=PydanticObjectId())
    claimed = SimpleNamespace(initial_input={"user_text": "generate report"})
    monkeypatch.setattr(
        executor,
        "_prepare_scheduled_run",
        AsyncMock(return_value=(claimed, definition, run)),
    )
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)
    completed_run = SimpleNamespace(status=WorkflowRunStatus.COMPLETED)
    runner = SimpleNamespace(run=AsyncMock(return_value=(completed_run, None)))
    repository = object()

    await executor._execute_schedule(schedule, runner, repository)

    runner.run.assert_awaited_once_with(
        definition_id=str(definition.id),
        user_text="generate report",
        auth_context=None,
        existing_run_id=str(run.id),
    )
    finish.assert_awaited_once_with(schedule, run, WorkflowRunStatus.COMPLETED, repository)


@pytest.mark.asyncio
async def test_execute_schedule_skips_when_workflow_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "_prepare_scheduled_run", AsyncMock(return_value=None))
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)
    runner = SimpleNamespace(run=AsyncMock())
    repository = object()

    await executor._execute_schedule(
        SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1"),
        runner,
        repository,
    )

    runner.run.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_schedule_persists_runner_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    definition = SimpleNamespace(id=PydanticObjectId())
    run = SimpleNamespace(id=PydanticObjectId())
    claimed = SimpleNamespace(initial_input={"user_text": "generate report"})
    monkeypatch.setattr(
        executor,
        "_prepare_scheduled_run",
        AsyncMock(return_value=(claimed, definition, run)),
    )
    mark_failed = AsyncMock()
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_mark_run_failed", mark_failed)
    monkeypatch.setattr(executor, "_finish_schedule", finish)
    error = RuntimeError("runner failed")
    runner = SimpleNamespace(run=AsyncMock(side_effect=error))
    repository = object()

    await executor._execute_schedule(schedule, runner, repository)

    mark_failed.assert_awaited_once_with(run, error)
    finish.assert_awaited_once_with(schedule, run, WorkflowRunStatus.FAILED, repository)


@pytest.mark.asyncio
async def test_mark_run_failed_updates_latest_persisted_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run = SimpleNamespace(id=PydanticObjectId())
    persisted_run = SimpleNamespace(save=AsyncMock())
    monkeypatch.setattr(executor.WorkflowRun, "get", AsyncMock(return_value=persisted_run))
    error = RuntimeError("execution failed")

    await executor._mark_run_failed(run, error)

    assert persisted_run.status == WorkflowRunStatus.FAILED
    assert persisted_run.error_summary == "execution failed"
    assert persisted_run.finished_at.tzinfo is UTC
    persisted_run.save.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_finish_schedule_uses_fencing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = SimpleNamespace(finish_claim=AsyncMock(return_value=False))
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    run = SimpleNamespace(id=PydanticObjectId())

    await executor._finish_schedule(schedule, run, WorkflowRunStatus.COMPLETED, repository)

    repository.finish_claim.assert_awaited_once_with(
        schedule.id,
        "lease-1",
        run.id,
        WorkflowRunStatus.COMPLETED,
    )


@pytest.mark.asyncio
async def test_renew_lease_stops_when_claim_is_superseded(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = SimpleNamespace(renew_claim=AsyncMock(return_value=False))
    monkeypatch.setattr(executor, "_MINIMUM_HEARTBEAT_INTERVAL_SECONDS", 0.001)

    await executor._renew_lease(repository, PydanticObjectId(), "lease-1", 0, asyncio.Event())

    repository.renew_claim.assert_awaited_once()
