import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from workflow_worker import main


def test_handle_task_done_removes_and_observes_completed_task() -> None:
    task = MagicMock(spec=asyncio.Task)
    in_flight = {task}

    main._handle_task_done(task, in_flight)

    assert not in_flight
    task.result.assert_called_once_with()


@pytest.mark.asyncio
async def test_main_initializes_and_closes_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_settings = SimpleNamespace(
        mongo_config=object(),
        redis_config=object(),
        redis_key_prefix="worker-prefix",
    )
    redis_client = object()
    directive_queue = object()
    cancellation_manager = object()
    http_client = SimpleNamespace(aclose=AsyncMock())
    runner = object()
    repository = object()
    loop = SimpleNamespace(add_signal_handler=MagicMock())
    init_mongodb = AsyncMock()
    run_scheduler_loop = AsyncMock()
    close_mongodb = AsyncMock()
    close_redis_client = MagicMock()

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "init_mongodb", init_mongodb)
    monkeypatch.setattr(main.MongoDB, "get_database", lambda: object())
    monkeypatch.setattr(main, "WorkflowScheduleRepository", lambda _database: repository)
    monkeypatch.setattr(main, "create_redis_client", lambda _config: redis_client)
    monkeypatch.setattr(main, "DirectiveQueue", lambda: directive_queue)
    monkeypatch.setattr(main, "MongoBackedCancellationManager", lambda **_kwargs: cancellation_manager)
    monkeypatch.setattr(main, "set_cancellation_manager", MagicMock())
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: http_client)
    monkeypatch.setattr(main, "_build_runner", lambda *_args: runner)
    monkeypatch.setattr(main.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(main, "_run_scheduler_loop", run_scheduler_loop)
    monkeypatch.setattr(main, "close_redis_client", close_redis_client)
    monkeypatch.setattr(main, "close_mongodb", close_mongodb)

    await main.main()

    init_mongodb.assert_awaited_once_with(mock_settings.mongo_config)
    run_scheduler_loop.assert_awaited_once()
    assert run_scheduler_loop.await_args.args[1] is runner
    assert run_scheduler_loop.await_args.args[2] is repository
    assert loop.add_signal_handler.call_count == 2
    http_client.aclose.assert_awaited_once_with()
    close_redis_client.assert_called_once_with(redis_client)
    close_mongodb.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_scheduler_loop_sleeps_until_next_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    mock_settings = SimpleNamespace(
        lease_duration_seconds=300,
        max_concurrent_runs=2,
        max_claimed_runs=4,
        max_sleep_seconds=30.0,
    )
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    claim = AsyncMock(return_value=None)
    peek = AsyncMock(return_value=deadline)
    repository = SimpleNamespace(claim_due=claim, peek_next_deadline=peek)

    async def wait_for_stop(awaitable: Awaitable[bool], timeout: float) -> bool:
        assert 0 < timeout <= 5
        stop_event.set()
        return await awaitable

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main.asyncio, "wait_for", wait_for_stop)

    await main._run_scheduler_loop(stop_event, SimpleNamespace(), repository)

    claim.assert_awaited_once_with(300)
    peek.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_scheduler_loop_immediately_continues_after_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    mock_settings = SimpleNamespace(
        lease_duration_seconds=120,
        max_concurrent_runs=1,
        max_claimed_runs=2,
        max_sleep_seconds=30.0,
    )
    schedule = SimpleNamespace()

    async def claim_once(_lease_seconds: int) -> object:
        stop_event.set()
        return schedule

    execute = AsyncMock()
    peek = AsyncMock()
    repository = SimpleNamespace(claim_due=claim_once, peek_next_deadline=peek)
    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "run_bounded", execute)

    await main._run_scheduler_loop(stop_event, SimpleNamespace(), repository)

    execute.assert_awaited_once()
    assert execute.await_args.args[0] is schedule
    assert execute.await_args.kwargs["lease_seconds"] == 120
    peek.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_loop_stops_claiming_at_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    release_runs = asyncio.Event()
    two_runs_started = asyncio.Event()
    claimed_count = 0
    started_count = 0
    mock_settings = SimpleNamespace(
        lease_duration_seconds=120,
        max_concurrent_runs=1,
        max_claimed_runs=2,
        max_sleep_seconds=30.0,
    )

    async def claim_schedule(_lease_seconds: int) -> object | None:
        nonlocal claimed_count
        claimed_count += 1
        if claimed_count <= 3:
            return SimpleNamespace(id=claimed_count)
        stop_event.set()
        return None

    async def execute(*_args: object, **_kwargs: object) -> None:
        nonlocal started_count
        started_count += 1
        if started_count == 2:
            two_runs_started.set()
        await release_runs.wait()

    repository = SimpleNamespace(
        claim_due=claim_schedule,
        peek_next_deadline=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "run_bounded", execute)

    scheduler_task = asyncio.create_task(main._run_scheduler_loop(stop_event, SimpleNamespace(), repository))
    await asyncio.wait_for(two_runs_started.wait(), timeout=1)

    assert claimed_count == 2

    release_runs.set()
    await asyncio.wait_for(scheduler_task, timeout=1)
    assert claimed_count == 4


@pytest.mark.asyncio
async def test_wait_for_claim_capacity_is_interrupted_by_stop_signal() -> None:
    stop_event = asyncio.Event()
    run_blocker = asyncio.Event()
    in_flight = {asyncio.create_task(run_blocker.wait())}

    capacity_waiter = asyncio.create_task(main._wait_for_claim_capacity(stop_event, in_flight, max_claimed_runs=1))
    await asyncio.sleep(0)
    stop_event.set()

    await asyncio.wait_for(capacity_waiter, timeout=1)
    assert not next(iter(in_flight)).done()

    run_blocker.set()
    await asyncio.gather(*in_flight)
