import asyncio
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
        encryption_key=b"0" * 32,
    )
    redis_client = object()
    directive_queue = object()
    cancellation_manager = object()
    http_client = SimpleNamespace(aclose=AsyncMock())
    azure_client_cache = SimpleNamespace(close=AsyncMock())
    azure_model_credential = SimpleNamespace(token_provider=object(), close=MagicMock())
    runner = object()
    repository = object()
    loop = SimpleNamespace(add_signal_handler=MagicMock())
    init_mongodb = AsyncMock()
    run_scheduler_loop = AsyncMock()
    close_mongodb = AsyncMock()
    close_redis_client = MagicMock()
    initialize_telemetry = MagicMock()
    shutdown_telemetry_safe = MagicMock()

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "_initialize_telemetry", initialize_telemetry)
    monkeypatch.setattr(main, "_shutdown_telemetry_safe", shutdown_telemetry_safe)
    monkeypatch.setattr(main, "init_mongodb", init_mongodb)
    monkeypatch.setattr(main.MongoDB, "get_database", object)
    monkeypatch.setattr(main, "WorkflowScheduleRepository", lambda _database: repository)
    monkeypatch.setattr(main, "create_redis_client", lambda _config: redis_client)
    monkeypatch.setattr(main, "DirectiveQueue", lambda: directive_queue)
    monkeypatch.setattr(main, "MongoBackedCancellationManager", lambda **_kwargs: cancellation_manager)
    monkeypatch.setattr(main, "set_cancellation_manager", MagicMock())
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: http_client)
    monkeypatch.setattr(main, "AzureFoundryClientCache", lambda **_kwargs: azure_client_cache)
    monkeypatch.setattr(main, "AzureModelCredential", lambda: azure_model_credential)
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
    azure_client_cache.close.assert_awaited_once_with()
    azure_model_credential.close.assert_called_once_with()
    close_redis_client.assert_called_once_with(redis_client)
    close_mongodb.assert_awaited_once_with()
    initialize_telemetry.assert_called_once_with()
    shutdown_telemetry_safe.assert_called_once_with()


def test_initialize_telemetry_is_best_effort_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every telemetry setup call may fail without crashing the worker startup."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("collector unreachable")

    mock_settings = SimpleNamespace(
        telemetry_config=SimpleNamespace(build_version="1.2.3"),
        configure_logging=boom,
    )

    basic_config = MagicMock()
    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main.logging, "basicConfig", basic_config)
    monkeypatch.setattr(main, "configure_structured_logging", boom)
    monkeypatch.setattr(main, "setup_metrics", boom)
    monkeypatch.setattr(main, "initialize_workflow_metrics", boom)
    monkeypatch.setattr(main, "setup_tracing", boom)

    # Must not raise even though every underlying call blows up.
    main._initialize_telemetry()

    basic_config.assert_called_once()


def test_initialize_telemetry_wires_setup_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    telemetry_config = SimpleNamespace(build_version="9.9.9")
    baseline_logging = MagicMock()
    mock_settings = SimpleNamespace(telemetry_config=telemetry_config, configure_logging=baseline_logging)
    configure_logging = MagicMock()
    setup_metrics = MagicMock()
    init_metrics = MagicMock()
    setup_tracing = MagicMock()

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "configure_structured_logging", configure_logging)
    monkeypatch.setattr(main, "setup_metrics", setup_metrics)
    monkeypatch.setattr(main, "initialize_workflow_metrics", init_metrics)
    monkeypatch.setattr(main, "setup_tracing", setup_tracing)

    main._initialize_telemetry()

    baseline_logging.assert_called_once_with("workflow_worker")
    configure_logging.assert_called_once_with(
        "workflow_worker",
        "registry_pkgs",
        service_name="workflow-worker",
        service_version="9.9.9",
    )
    setup_metrics.assert_called_once_with("workflow-worker", telemetry_config)
    init_metrics.assert_called_once_with(telemetry_config)
    setup_tracing.assert_called_once_with("workflow-worker", telemetry_config)


def test_shutdown_telemetry_safe_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("shutdown failed")

    monkeypatch.setattr(main, "shutdown_telemetry", boom)

    # Must swallow the failure so graceful shutdown always completes.
    main._shutdown_telemetry_safe()


@pytest.mark.asyncio
async def test_main_shuts_down_telemetry_after_scheduler_drains(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telemetry shutdown must run only after the scheduler loop (which drains in-flight runs) returns."""
    call_order: list[str] = []
    mock_settings = SimpleNamespace(
        mongo_config=object(),
        redis_config=object(),
        redis_key_prefix="worker-prefix",
        encryption_key=b"0" * 32,
    )

    async def scheduler_loop(*_args: object) -> None:
        call_order.append("scheduler")

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "_initialize_telemetry", MagicMock())
    monkeypatch.setattr(main, "_shutdown_telemetry_safe", lambda: call_order.append("shutdown"))
    monkeypatch.setattr(main, "init_mongodb", AsyncMock())
    monkeypatch.setattr(main.MongoDB, "get_database", object)
    monkeypatch.setattr(main, "WorkflowScheduleRepository", lambda _database: object())
    monkeypatch.setattr(main, "create_redis_client", lambda _config: object())
    monkeypatch.setattr(main, "DirectiveQueue", object)
    monkeypatch.setattr(main, "MongoBackedCancellationManager", lambda **_kwargs: object())
    monkeypatch.setattr(main, "set_cancellation_manager", MagicMock())
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_kwargs: SimpleNamespace(aclose=AsyncMock()))
    monkeypatch.setattr(main, "AzureFoundryClientCache", lambda **_kwargs: SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(
        main,
        "AzureModelCredential",
        lambda: SimpleNamespace(token_provider=object(), close=MagicMock()),
    )
    monkeypatch.setattr(main, "_build_runner", lambda *_args: object())
    monkeypatch.setattr(main.asyncio, "get_running_loop", lambda: SimpleNamespace(add_signal_handler=MagicMock()))
    monkeypatch.setattr(main, "_run_scheduler_loop", scheduler_loop)
    monkeypatch.setattr(main, "close_redis_client", MagicMock())
    monkeypatch.setattr(main, "close_mongodb", AsyncMock())

    await main.main()

    assert call_order == ["scheduler", "shutdown"]


def test_build_runner_supplies_a2a_headers_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_settings = SimpleNamespace(
        workflow_llm_model_id="model",
        aws_region="us-east-1",
        aws_access_key_id=None,
        aws_secret_access_key=None,
        aws_session_token=None,
        encryption_key=b"test-key",
        jwt_signing_config=object(),
        redis_key_prefix="worker-prefix",
    )
    headers_provider = object()
    mcp_headers_provider = object()
    fallback_model = object()
    model_factory = MagicMock(return_value=fallback_model)
    captured: dict[str, object] = {}

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "build_legacy_bedrock_model", model_factory)
    monkeypatch.setattr(main.MongoDB, "get_client", lambda: object())
    monkeypatch.setattr(main.MongoDB, "database_name", "jarvis", raising=False)
    monkeypatch.setattr(main, "make_a2a_headers_provider", lambda **kwargs: headers_provider)
    monkeypatch.setattr(main, "_build_mcp_headers_provider", lambda _redis: mcp_headers_provider)
    monkeypatch.setattr(main, "WorkflowRunner", lambda **kwargs: captured.update(kwargs) or object())

    azure_client_cache = object()
    azure_model_credential = SimpleNamespace(token_provider=object())
    main._build_runner(object(), object(), object(), azure_client_cache, azure_model_credential)

    model_factory.assert_called_once_with("model", "us-east-1")
    assert captured["headers_provider"] is headers_provider
    assert captured["fallback_model"] is fallback_model
    assert captured["mcp_headers_provider"] is mcp_headers_provider
    assert captured["azure_ad_token_provider"] is azure_model_credential.token_provider


def test_build_mcp_headers_provider_is_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_settings = SimpleNamespace(
        encryption_key=b"0" * 32,
        registry_app_name="jarvis-registry",
        registry_client_url="http://localhost:5173",
        redis_key_prefix="worker-prefix",
        jwt_signing_config=object(),
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "UserService", object)
    monkeypatch.setattr(main, "TokenService", lambda **_kwargs: object())
    monkeypatch.setattr(main, "FlowStateManager", lambda **_kwargs: object())
    monkeypatch.setattr(main, "MCPOAuthService", lambda **_kwargs: object())
    monkeypatch.setattr(main, "make_mcp_headers_provider", lambda **kwargs: captured.update(kwargs) or object())

    main._build_mcp_headers_provider(object())

    # Scheduled runs must be non-interactive so no Redis OAuth flow is minted.
    assert captured["interactive"] is False
    assert captured["scope_resolver"]({"scopes": ["servers-read"]}) == ["servers-read"]


def _loop_settings(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "lease_duration_seconds": 300,
        "max_concurrent_runs": 2,
        "max_claimed_runs": 4,
        "max_sleep_seconds": 30.0,
        "shutdown_drain_seconds": 40.0,
        "shutdown_finalize_seconds": 10.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_scheduler_loop_sleeps_until_next_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    mock_settings = _loop_settings()
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    claim = AsyncMock(return_value=None)
    peek = AsyncMock(return_value=deadline)
    repository = SimpleNamespace(claim_due=claim, peek_next_deadline=peek)

    async def fake_sleep(ev: asyncio.Event, timeout: float) -> bool:
        assert 0 < timeout <= 5
        ev.set()
        return True

    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "interruptible_sleep", fake_sleep)

    await main._run_scheduler_loop(stop_event, SimpleNamespace(), repository, "owner-1")

    claim.assert_awaited_once_with(300)
    peek.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_scheduler_loop_immediately_continues_after_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    mock_settings = _loop_settings(lease_duration_seconds=120, max_concurrent_runs=1, max_claimed_runs=2)
    schedule = SimpleNamespace()

    async def claim_once(_lease_seconds: int) -> object:
        stop_event.set()
        return schedule

    execute = AsyncMock()
    peek = AsyncMock()
    repository = SimpleNamespace(claim_due=claim_once, peek_next_deadline=peek)
    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "run_bounded", execute)

    await main._run_scheduler_loop(stop_event, SimpleNamespace(), repository, "owner-1")

    # Called (coroutine built + task created); the task is cancelled by the shutdown drain before it
    # runs, so assert on the call args rather than the await.
    execute.assert_called_once()
    assert execute.call_args.args[0] is schedule
    assert execute.call_args.args[4] == "owner-1"
    assert execute.call_args.kwargs["lease_seconds"] == 120
    peek.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_loop_stops_claiming_at_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    release_runs = asyncio.Event()
    two_runs_started = asyncio.Event()
    claimed_count = 0
    started_count = 0
    mock_settings = _loop_settings(lease_duration_seconds=120, max_concurrent_runs=1, max_claimed_runs=2)

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

    scheduler_task = asyncio.create_task(main._run_scheduler_loop(stop_event, SimpleNamespace(), repository, "owner-1"))
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


@pytest.mark.asyncio
async def test_drain_in_flight_lets_running_task_finish_within_drain() -> None:
    done_flag = asyncio.Event()

    async def quick() -> None:
        await asyncio.sleep(0.01)
        done_flag.set()

    task: asyncio.Task[None] = asyncio.create_task(quick())
    await asyncio.sleep(0)

    await main._drain_in_flight({task}, {task}, drain_seconds=1.0, finalize_seconds=0.05)

    assert task.done() and not task.cancelled()
    assert done_flag.is_set()


@pytest.mark.asyncio
async def test_drain_in_flight_cancels_queued_and_finalizes_running() -> None:
    async def forever() -> None:
        await asyncio.Event().wait()

    queued: asyncio.Task[None] = asyncio.create_task(forever())
    running: asyncio.Task[None] = asyncio.create_task(forever())
    await asyncio.sleep(0)

    # queued is NOT in executing → cancelled immediately; running is → cancelled only after the drain.
    await main._drain_in_flight({queued, running}, {running}, drain_seconds=0.05, finalize_seconds=0.1)

    assert queued.cancelled()
    assert running.cancelled()


@pytest.mark.asyncio
async def test_scheduler_loop_returns_within_shutdown_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that never finishes on its own must not block shutdown past drain + finalize (+1s)."""
    stop_event = asyncio.Event()
    mock_settings = _loop_settings(
        lease_duration_seconds=120,
        max_concurrent_runs=1,
        max_claimed_runs=2,
        shutdown_drain_seconds=0.1,
        shutdown_finalize_seconds=0.1,
    )
    never = asyncio.Event()
    claimed = False

    async def claim(_lease_seconds: int) -> object | None:
        nonlocal claimed
        if not claimed:
            claimed = True
            return SimpleNamespace(id=1, lease_token="lease-1")
        stop_event.set()
        return None

    async def run_forever(
        _schedule: object,
        _semaphore: object,
        _runner: object,
        _repository: object,
        _lease_owner: str,
        executing: set[asyncio.Task[None]],
        lease_seconds: int = 300,
    ) -> None:
        current = asyncio.current_task()
        assert current is not None
        executing.add(current)
        try:
            await never.wait()
        finally:
            executing.discard(current)

    repository = SimpleNamespace(claim_due=claim, peek_next_deadline=AsyncMock(return_value=None))
    monkeypatch.setattr(main, "settings", mock_settings)
    monkeypatch.setattr(main, "run_bounded", run_forever)

    start = asyncio.get_event_loop().time()
    await asyncio.wait_for(
        main._run_scheduler_loop(stop_event, SimpleNamespace(), repository, "owner-1"),
        timeout=0.1 + 0.1 + 1,
    )
    assert asyncio.get_event_loop().time() - start < 0.1 + 0.1 + 1
