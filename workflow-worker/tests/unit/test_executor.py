import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from workflow_worker import executor


def _claimed_schedule(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "id": PydanticObjectId(),
        "workflow_definition_id": PydanticObjectId(),
        "lease_token": "lease-1",
        "cron_expression": "0 2 * * *",
        "timezone": "UTC",
        "initial_input": {"user_text": "generate report"},
        "created_by": PydanticObjectId(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _definition(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "id": PydanticObjectId(),
        "version": 3,
        "enabled": True,
        "model_dump": lambda mode="json": {"name": "wf"},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def _workflow_run_settings_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """WorkflowRun() calls get_pymongo_collection() in Beanie's __init__; stub settings so
    constructing one in tests doesn't require a real Beanie/Mongo connection."""
    monkeypatch.setattr(executor.WorkflowRun, "get_settings", lambda: SimpleNamespace(pymongo_collection=None))


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
async def test_create_scheduled_run_maps_fields_and_inserts_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = _claimed_schedule()
    definition = _definition()
    next_run_at = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(executor, "calculate_next_run_at", lambda cron, tz: next_run_at)
    repository = SimpleNamespace(advance_and_insert_run=AsyncMock(return_value=True))

    run = await executor._create_scheduled_run(claimed, definition, repository)

    assert run is not None
    assert run.workflow_definition_id == definition.id
    assert run.workflow_version == definition.version
    assert run.status == WorkflowRunStatus.PENDING
    assert run.trigger_source == "schedule"
    assert run.initial_input == claimed.initial_input
    assert run.definition_snapshot == {"name": "wf"}
    assert run.triggering_user_id == str(claimed.created_by)
    repository.advance_and_insert_run.assert_awaited_once_with(claimed, run, next_run_at)


@pytest.mark.asyncio
async def test_create_scheduled_run_returns_none_when_fencing_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = _claimed_schedule()
    definition = _definition()
    monkeypatch.setattr(executor, "calculate_next_run_at", lambda cron, tz: datetime.now(UTC))
    repository = SimpleNamespace(advance_and_insert_run=AsyncMock(return_value=False))

    run = await executor._create_scheduled_run(claimed, definition, repository)

    assert run is None


@pytest.mark.asyncio
async def test_prepare_scheduled_run_returns_none_when_claim_is_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    repository = SimpleNamespace(load_claim=AsyncMock(return_value=None))
    definition_get = AsyncMock()
    monkeypatch.setattr(executor.WorkflowDefinition, "get", definition_get)

    result = await executor._prepare_scheduled_run(schedule, repository)

    assert result is None
    repository.load_claim.assert_awaited_once_with(schedule.id, schedule.lease_token)
    definition_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_scheduled_run_disables_schedule_when_definition_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    claimed = _claimed_schedule()
    repository = SimpleNamespace(
        load_claim=AsyncMock(return_value=claimed),
        disable_claim=AsyncMock(return_value=True),
        advance_and_insert_run=AsyncMock(),
    )
    monkeypatch.setattr(executor.WorkflowDefinition, "get", AsyncMock(return_value=None))

    result = await executor._prepare_scheduled_run(schedule, repository)

    assert result is None
    repository.disable_claim.assert_awaited_once_with(claimed.id, claimed.lease_token)
    repository.advance_and_insert_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_scheduled_run_disables_schedule_when_definition_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    claimed = _claimed_schedule()
    definition = _definition(enabled=False)
    repository = SimpleNamespace(
        load_claim=AsyncMock(return_value=claimed),
        disable_claim=AsyncMock(return_value=True),
        advance_and_insert_run=AsyncMock(),
    )
    monkeypatch.setattr(executor.WorkflowDefinition, "get", AsyncMock(return_value=definition))

    result = await executor._prepare_scheduled_run(schedule, repository)

    assert result is None
    repository.disable_claim.assert_awaited_once_with(claimed.id, claimed.lease_token)
    repository.advance_and_insert_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_scheduled_run_skips_when_fencing_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    claimed = _claimed_schedule()
    definition = _definition()
    repository = SimpleNamespace(
        load_claim=AsyncMock(return_value=claimed),
        disable_claim=AsyncMock(),
        advance_and_insert_run=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(executor.WorkflowDefinition, "get", AsyncMock(return_value=definition))
    monkeypatch.setattr(executor, "calculate_next_run_at", lambda cron, tz: datetime.now(UTC))

    result = await executor._prepare_scheduled_run(schedule, repository)

    assert result is None
    repository.disable_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_scheduled_run_returns_claim_definition_and_run_on_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    claimed = _claimed_schedule()
    definition = _definition()
    repository = SimpleNamespace(
        load_claim=AsyncMock(return_value=claimed),
        disable_claim=AsyncMock(),
        advance_and_insert_run=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(executor.WorkflowDefinition, "get", AsyncMock(return_value=definition))
    monkeypatch.setattr(executor, "calculate_next_run_at", lambda cron, tz: datetime.now(UTC))

    result = await executor._prepare_scheduled_run(schedule, repository)

    assert result is not None
    result_claimed, result_definition, result_run = result
    assert result_claimed is claimed
    assert result_definition is definition
    assert result_run.workflow_definition_id == definition.id
    repository.disable_claim.assert_not_awaited()


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
