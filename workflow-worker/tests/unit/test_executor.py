import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.workflows import run_lease
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
    executing: set[asyncio.Task[None]] = set()

    await executor.run_bounded(schedule, semaphore, runner, repository, "owner-1", executing, lease_seconds=120)

    execute.assert_awaited_once_with(schedule, runner, repository, "owner-1")
    renew.assert_awaited_once()
    assert renew.await_args.args[:4] == (repository, schedule.id, "lease-1", 120)
    assert semaphore._value == 1
    assert executing == set()


@pytest.mark.asyncio
async def test_run_bounded_rejects_claim_without_lease_token(monkeypatch: pytest.MonkeyPatch) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(executor, "_execute_schedule", execute)

    await executor.run_bounded(
        SimpleNamespace(id=PydanticObjectId(), lease_token=None),
        asyncio.Semaphore(1),
        SimpleNamespace(),
        object(),
        "owner-1",
        set(),
    )

    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_bounded_releases_claim_when_cancelled_while_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    """A task cancelled before entering the semaphore releases its schedule claim and never executes."""
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    execute = AsyncMock()
    monkeypatch.setattr(executor, "_execute_schedule", execute)
    monkeypatch.setattr(executor, "_renew_lease", AsyncMock())
    release = AsyncMock(return_value=True)
    repository = SimpleNamespace(release_claim=release)
    # Semaphore already exhausted, so run_bounded blocks on `async with semaphore`.
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()
    executing: set[asyncio.Task[None]] = set()

    task = asyncio.create_task(
        executor.run_bounded(schedule, semaphore, SimpleNamespace(), repository, "owner-1", executing)
    )
    await asyncio.sleep(0)  # let it reach the semaphore wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    release.assert_awaited_once_with(schedule.id, "lease-1")
    execute.assert_not_awaited()
    assert executing == set()


@pytest.mark.asyncio
async def test_run_bounded_does_not_release_claim_when_cancelled_while_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation while executing is owned by execute_leased_run/_finish_schedule, not release_claim."""
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    monkeypatch.setattr(executor, "_renew_lease", AsyncMock())
    release = AsyncMock()
    repository = SimpleNamespace(release_claim=release)
    started = asyncio.Event()

    async def blocking_execute(*_args: object, **_kwargs: object) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(executor, "_execute_schedule", blocking_execute)
    executing: set[asyncio.Task[None]] = set()

    task = asyncio.create_task(
        executor.run_bounded(schedule, asyncio.Semaphore(1), SimpleNamespace(), repository, "owner-1", executing)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    assert len(executing) == 1  # entered the semaphore → registered as executing

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    release.assert_not_awaited()
    assert executing == set()


def _lease(run_id: PydanticObjectId) -> SimpleNamespace:
    return SimpleNamespace(doc_id=run_id, owner="workflow-worker-x", token="run-token", expires_at=None)


@pytest.mark.asyncio
async def test_execute_schedule_runs_under_lease_and_finishes_with_runner_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    definition = SimpleNamespace(id=PydanticObjectId())
    run = SimpleNamespace(id=PydanticObjectId())
    lease = _lease(run.id)
    claimed = SimpleNamespace(initial_input={"user_text": "generate report"}, created_by=PydanticObjectId())
    monkeypatch.setattr(
        executor,
        "_prepare_scheduled_run",
        AsyncMock(return_value=(claimed, definition, run, lease)),
    )
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)
    completed_run = SimpleNamespace(status=WorkflowRunStatus.COMPLETED)
    # execute_leased_run swallows runner.run (a coroutine), so give the runner a real coroutine
    # and have the patched execute_leased_run close it and return the completed run.
    runner = SimpleNamespace(run=AsyncMock(return_value=(completed_run, None)))

    async def fake_execute_leased_run(lease_arg: object, coro: object, *, interrupted_reason: str) -> object:
        assert lease_arg is lease
        assert interrupted_reason == executor._WORKER_SHUTDOWN_REASON
        return await coro

    monkeypatch.setattr(executor, "execute_leased_run", fake_execute_leased_run)
    repository = object()

    await executor._execute_schedule(schedule, runner, repository, "owner-1")

    _args, kwargs = runner.run.await_args
    assert kwargs["definition_id"] == str(definition.id)
    assert kwargs["user_text"] == "generate report"
    assert kwargs["existing_run_id"] == str(run.id)
    assert isinstance(kwargs["run_writer"], executor.LeasedRunStateWriter)
    finish.assert_awaited_once_with(schedule, run, WorkflowRunStatus.COMPLETED, repository)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        WorkflowRunStatus.AWAITING_APPROVAL,
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.CANCELLED,
    ],
)
async def test_execute_schedule_reads_status_when_lease_lost(
    monkeypatch: pytest.MonkeyPatch,
    status: WorkflowRunStatus,
) -> None:
    """When execute_leased_run returns None (lease lost), the final status is read back from Mongo."""
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    definition = SimpleNamespace(id=PydanticObjectId())
    run = SimpleNamespace(id=PydanticObjectId())
    lease = _lease(run.id)
    claimed = SimpleNamespace(initial_input={"user_text": "x"}, created_by=PydanticObjectId())
    monkeypatch.setattr(
        executor,
        "_prepare_scheduled_run",
        AsyncMock(return_value=(claimed, definition, run, lease)),
    )
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)

    async def fake_execute_leased_run(_lease: object, coro: object, *, interrupted_reason: str) -> None:
        coro.close()  # lease lost before the coroutine ran
        return None

    monkeypatch.setattr(executor, "execute_leased_run", fake_execute_leased_run)
    monkeypatch.setattr(executor, "_read_run_status", AsyncMock(return_value=status))
    runner = SimpleNamespace(run=AsyncMock(return_value=(SimpleNamespace(), None)))

    await executor._execute_schedule(schedule, runner, repository := object(), "owner-1")

    finish.assert_awaited_once_with(schedule, run, status, repository)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED],
)
async def test_execute_schedule_maps_leased_status_to_failed_when_lease_lost(
    monkeypatch: pytest.MonkeyPatch,
    status: WorkflowRunStatus,
) -> None:
    """When execute_leased_run returns None and the run is still leased, finish with FAILED."""
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    definition = SimpleNamespace(id=PydanticObjectId())
    run = SimpleNamespace(id=PydanticObjectId())
    lease = _lease(run.id)
    claimed = SimpleNamespace(initial_input={"user_text": "x"}, created_by=PydanticObjectId())
    monkeypatch.setattr(
        executor,
        "_prepare_scheduled_run",
        AsyncMock(return_value=(claimed, definition, run, lease)),
    )
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)

    async def fake_execute_leased_run(_lease: object, coro: object, *, interrupted_reason: str) -> None:
        coro.close()
        return None

    monkeypatch.setattr(executor, "execute_leased_run", fake_execute_leased_run)
    monkeypatch.setattr(executor, "_read_run_status", AsyncMock(return_value=status))
    runner = SimpleNamespace(run=AsyncMock(return_value=(SimpleNamespace(), None)))

    await executor._execute_schedule(schedule, runner, repository := object(), "owner-1")

    finish.assert_awaited_once_with(schedule, run, WorkflowRunStatus.FAILED, repository)


@pytest.mark.asyncio
async def test_execute_schedule_shutdown_cancel_fails_run_and_finishes_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end (real execute_leased_run): cancelling an in-flight run writes FAILED with the
    worker shutdown reason, fails open NodeRuns, and finishes the schedule with last_run_status=FAILED.
    """
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    definition = SimpleNamespace(id=PydanticObjectId())
    run_id = PydanticObjectId()
    run = SimpleNamespace(id=run_id)
    lease = run_lease.workflow_run_lease_repository.new_lease("workflow-worker-x", doc_id=run_id)
    claimed = SimpleNamespace(initial_input={"user_text": "x"}, created_by=PydanticObjectId())
    monkeypatch.setattr(
        executor,
        "_prepare_scheduled_run",
        AsyncMock(return_value=(claimed, definition, run, lease)),
    )
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)

    # Stub the lease repository's fenced writes and NodeRun fail-open so no real Mongo is touched.
    transition = AsyncMock(return_value=True)
    fail_open = AsyncMock(return_value=0)
    monkeypatch.setattr(run_lease.workflow_run_lease_repository, "transition", transition)
    monkeypatch.setattr(run_lease, "fail_open_node_runs", fail_open)

    started = asyncio.Event()

    async def blocking_run(**_kwargs: object) -> tuple[object, object]:
        started.set()
        await asyncio.Event().wait()  # never completes on its own
        raise AssertionError("unreachable")

    runner = SimpleNamespace(run=blocking_run)
    repository = object()

    task = asyncio.create_task(executor._execute_schedule(schedule, runner, repository, "owner-1"))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # on_cancelled wrote FAILED with the worker reason, fenced + releasing the run lease.
    transition.assert_awaited_once()
    _lease_arg, set_fields = transition.await_args.args
    assert set_fields["status"] == WorkflowRunStatus.FAILED.value
    assert set_fields["error_summary"] == executor._WORKER_SHUTDOWN_REASON
    assert transition.await_args.kwargs["release"] is True
    fail_open.assert_awaited_once_with(run_id, executor._WORKER_SHUTDOWN_REASON)
    # Schedule is finished with FAILED (claim released via finish_claim inside _finish_schedule).
    finish.assert_awaited_once_with(schedule, run, WorkflowRunStatus.FAILED, repository)


@pytest.mark.asyncio
async def test_execute_schedule_skips_when_workflow_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "_prepare_scheduled_run", AsyncMock(return_value=None))
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)
    execute_leased = AsyncMock()
    monkeypatch.setattr(executor, "execute_leased_run", execute_leased)
    runner = SimpleNamespace(run=AsyncMock())
    repository = object()

    await executor._execute_schedule(
        SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1"),
        runner,
        repository,
        "owner-1",
    )

    execute_leased.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_schedule_finishes_failed_when_runner_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_leased_run already wrote FAILED (fenced) and re-raised; _execute_schedule just finishes."""
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    definition = SimpleNamespace(id=PydanticObjectId())
    run = SimpleNamespace(id=PydanticObjectId())
    lease = _lease(run.id)
    claimed = SimpleNamespace(initial_input={"user_text": "x"}, created_by=PydanticObjectId())
    monkeypatch.setattr(
        executor,
        "_prepare_scheduled_run",
        AsyncMock(return_value=(claimed, definition, run, lease)),
    )
    finish = AsyncMock()
    monkeypatch.setattr(executor, "_finish_schedule", finish)

    async def fake_execute_leased_run(_lease: object, coro: object, *, interrupted_reason: str) -> object:
        coro.close()  # don't actually run it
        raise RuntimeError("runner failed")

    monkeypatch.setattr(executor, "execute_leased_run", fake_execute_leased_run)
    runner = SimpleNamespace(run=AsyncMock(return_value=(SimpleNamespace(), None)))
    repository = object()

    await executor._execute_schedule(schedule, runner, repository, "owner-1")

    finish.assert_awaited_once_with(schedule, run, WorkflowRunStatus.FAILED, repository)


def test_scheduled_run_auth_context_uses_creator_as_user_id() -> None:
    created_by = PydanticObjectId()

    ctx = executor._scheduled_run_auth_context(created_by)

    assert ctx["user_id"] == str(created_by)
    assert ctx["client_id"] == "workflow-worker"
    assert ctx["groups"] == []
    assert ctx["scopes"] == []
    assert ctx["auth_source"] == "workflow_schedule"


@pytest.mark.asyncio
async def test_create_scheduled_run_maps_fields_and_inserts_leased_run(monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = _claimed_schedule()
    definition = _definition()
    next_run_at = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(executor, "calculate_next_run_at", lambda cron, tz: next_run_at)
    repository = SimpleNamespace(advance_and_insert_run=AsyncMock(return_value=True))

    created = await executor._create_scheduled_run(claimed, definition, repository, "workflow-worker-abc")

    assert created is not None
    run, lease = created
    assert run.workflow_definition_id == definition.id
    assert run.workflow_version == definition.version
    assert run.status == WorkflowRunStatus.PENDING
    assert run.trigger_source == "schedule"
    assert run.initial_input == claimed.initial_input
    assert run.definition_snapshot == {"name": "wf"}
    assert run.triggering_user_id == str(claimed.created_by)
    # Born already leased: owner starts with the worker prefix, non-null token, future expiry.
    assert run.id == lease.doc_id
    assert run.lease_owner == "workflow-worker-abc"
    assert run.lease_token == lease.token
    assert run.lease_token is not None
    assert run.lease_expires_at == lease.expires_at
    assert run.lease_expires_at > datetime.now(UTC)
    repository.advance_and_insert_run.assert_awaited_once_with(claimed, run, next_run_at)


@pytest.mark.asyncio
async def test_create_scheduled_run_returns_none_when_fencing_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = _claimed_schedule()
    definition = _definition()
    monkeypatch.setattr(executor, "calculate_next_run_at", lambda cron, tz: datetime.now(UTC))
    repository = SimpleNamespace(advance_and_insert_run=AsyncMock(return_value=False))

    created = await executor._create_scheduled_run(claimed, definition, repository, "workflow-worker-abc")

    assert created is None


@pytest.mark.asyncio
async def test_prepare_scheduled_run_returns_none_when_claim_is_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule = SimpleNamespace(id=PydanticObjectId(), lease_token="lease-1")
    repository = SimpleNamespace(load_claim=AsyncMock(return_value=None))
    definition_get = AsyncMock()
    monkeypatch.setattr(executor.WorkflowDefinition, "get", definition_get)

    result = await executor._prepare_scheduled_run(schedule, repository, "owner-1")

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

    result = await executor._prepare_scheduled_run(schedule, repository, "owner-1")

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

    result = await executor._prepare_scheduled_run(schedule, repository, "owner-1")

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

    result = await executor._prepare_scheduled_run(schedule, repository, "owner-1")

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

    result = await executor._prepare_scheduled_run(schedule, repository, "owner-1")

    assert result is not None
    result_claimed, result_definition, result_run, result_lease = result
    assert result_claimed is claimed
    assert result_definition is definition
    assert result_run.workflow_definition_id == definition.id
    assert result_run.id == result_lease.doc_id
    assert result_run.lease_owner == "owner-1"
    repository.disable_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_run_status_projects_status(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = PydanticObjectId()
    collection = SimpleNamespace(find_one=AsyncMock(return_value={"status": WorkflowRunStatus.COMPLETED.value}))
    monkeypatch.setattr(executor.WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: collection))

    status = await executor._read_run_status(run_id)

    assert status == WorkflowRunStatus.COMPLETED
    flt, projection = collection.find_one.await_args.args
    assert flt == {"_id": run_id}
    assert projection == {"status": 1}


@pytest.mark.asyncio
async def test_read_run_status_defaults_to_failed_when_run_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = SimpleNamespace(find_one=AsyncMock(return_value=None))
    monkeypatch.setattr(executor.WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: collection))

    assert await executor._read_run_status(PydanticObjectId()) == WorkflowRunStatus.FAILED


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

    async def no_sleep(_stop: asyncio.Event, _timeout: float) -> bool:
        return False

    monkeypatch.setattr(executor, "interruptible_sleep", no_sleep)

    # lease_seconds large so the lapse check doesn't fire before the first renew.
    await executor._renew_lease(repository, PydanticObjectId(), "lease-1", 120, asyncio.Event())

    repository.renew_claim.assert_awaited_once()


@pytest.mark.asyncio
async def test_renew_lease_tolerates_transient_errors_and_keeps_renewing(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    calls = 0

    async def renew(*_args: object) -> bool:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("mongo blip")
        if calls >= 4:
            stop_event.set()  # end the loop after proving it kept renewing past the errors
        return True

    repository = SimpleNamespace(renew_claim=renew)

    async def fake_sleep(ev: asyncio.Event, _timeout: float) -> bool:
        return ev.is_set()

    monkeypatch.setattr(executor, "interruptible_sleep", fake_sleep)

    await asyncio.wait_for(
        executor._renew_lease(repository, PydanticObjectId(), "lease-1", 120, stop_event),
        timeout=1,
    )

    assert calls == 4  # two exceptions survived, then two successful renewals


@pytest.mark.asyncio
async def test_renew_lease_returns_once_claim_lapses(monkeypatch: pytest.MonkeyPatch) -> None:
    """When renew_claim keeps failing long enough that locked_until passes, the loop gives up."""
    renew = AsyncMock(side_effect=RuntimeError("mongo down"))
    repository = SimpleNamespace(renew_claim=renew)

    async def tick(_stop: asyncio.Event, _timeout: float) -> bool:
        await asyncio.sleep(0.02)
        return False

    monkeypatch.setattr(executor, "interruptible_sleep", tick)

    # lease_seconds 0.05: after ~3 ticks of 0.02s, now passes last_renewed_until and the loop returns.
    await asyncio.wait_for(
        executor._renew_lease(repository, PydanticObjectId(), "lease-1", 0.05, stop_event=asyncio.Event()),
        timeout=1,
    )

    assert renew.await_count >= 1
