import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import Any

import httpx
from agno.models.aws import AwsBedrock
from agno.run.cancel import set_cancellation_manager

from registry_pkgs.core.structured_logging import configure_structured_logging
from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.database.redis_client import close_redis_client, create_redis_client
from registry_pkgs.federation.azure_foundry_client_cache import AzureFoundryClientCache
from registry_pkgs.oauth.flow_state_manager import FlowStateManager
from registry_pkgs.oauth.headers import HeaderBuildConfig
from registry_pkgs.oauth.oauth_service import MCPOAuthService
from registry_pkgs.oauth.token_service import TokenService
from registry_pkgs.oauth.user_service import UserService
from registry_pkgs.telemetry import setup_metrics, setup_tracing, shutdown_telemetry
from registry_pkgs.telemetry.workflow_metrics import initialize_workflow_metrics
from registry_pkgs.workflows.a2a_headers_provider import make_a2a_headers_provider
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.hitl import MongoBackedCancellationManager
from registry_pkgs.workflows.mcp_headers_provider import McpHeadersProvider, make_mcp_headers_provider
from registry_pkgs.workflows.runner import WorkflowRunner
from registry_pkgs.workflows.schedule_repository import WorkflowScheduleRepository
from workflow_worker.config import settings
from workflow_worker.executor import run_bounded
from workflow_worker.scheduler import calculate_sleep_seconds

logger = logging.getLogger("workflow_worker.main")

_SERVICE_NAME = "workflow-worker"


def _initialize_telemetry() -> None:
    """Best-effort telemetry setup that should not block the worker from starting."""
    try:
        settings.configure_logging("workflow_worker")
    except Exception:
        logger.warning("Failed to configure baseline logging", exc_info=True)
    try:
        configure_structured_logging(
            "workflow_worker",
            "registry_pkgs",
            service_name=_SERVICE_NAME,
            service_version=settings.telemetry_config.build_version,
        )
    except Exception:
        logger.warning("Failed to configure structured logging", exc_info=True)
    try:
        setup_metrics(_SERVICE_NAME, settings.telemetry_config)
        initialize_workflow_metrics(settings.telemetry_config)
    except Exception:
        logger.warning("Failed to initialize metrics", exc_info=True)
    try:
        setup_tracing(_SERVICE_NAME, settings.telemetry_config)
    except Exception:
        logger.warning("Failed to initialize tracing", exc_info=True)


def _shutdown_telemetry_safe() -> None:
    """Best-effort telemetry shutdown that never raises."""
    try:
        shutdown_telemetry()
    except Exception:
        logger.warning("Failed to shutdown telemetry", exc_info=True)


def _handle_task_done(task: asyncio.Task[None], in_flight: set[asyncio.Task[None]]) -> None:
    """Remove a completed task and surface unexpected executor failures."""
    in_flight.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        logger.info("Scheduled workflow execution task was cancelled")
    except Exception:
        logger.exception("Scheduled workflow execution task failed unexpectedly")


async def _wait_for_claim_capacity(
    stop_event: asyncio.Event,
    in_flight: set[asyncio.Task[None]],
    max_claimed_runs: int,
) -> None:
    """Apply backpressure when this worker already owns its maximum number of claims."""
    if len(in_flight) < max_claimed_runs:
        return

    stop_waiter = asyncio.create_task(stop_event.wait())
    try:
        await asyncio.wait({*in_flight, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
        # Let task done callbacks remove completed executions from in_flight.
        await asyncio.sleep(0)
    finally:
        if not stop_waiter.done():
            stop_waiter.cancel()
        await asyncio.gather(stop_waiter, return_exceptions=True)


def _build_runner(
    directive_queue: DirectiveQueue,
    redis_client: Any,
    http_client: httpx.AsyncClient,
    azure_client_cache: AzureFoundryClientCache,
) -> WorkflowRunner:
    """Construct the workflow runner and its A2A authentication provider."""
    llm = AwsBedrock(
        id=settings.workflow_llm_model_id,
        aws_region=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        aws_session_token=settings.aws_session_token,
    )
    headers_provider = make_a2a_headers_provider(
        jwt_config=settings.jwt_signing_config,
        azure_client_cache=azure_client_cache,
    )
    return WorkflowRunner(
        llm=llm,
        db_client=MongoDB.get_client(),
        db_name=MongoDB.database_name,
        jwt_config=settings.jwt_signing_config,
        directive_queue=directive_queue,
        a2a_httpx_client=http_client,
        headers_provider=headers_provider,
        mcp_headers_provider=_build_mcp_headers_provider(redis_client),
        redis_client=redis_client,
        redis_key_prefix=settings.redis_key_prefix,
    )


def _build_mcp_headers_provider(redis_client: Any) -> McpHeadersProvider:
    """Build the non-interactive MCP OAuth headers provider for scheduled runs."""
    user_service = UserService()
    token_service = TokenService(user_service=user_service, encryption_key=settings.encryption_key)
    flow_state_manager = FlowStateManager(
        redis_client=redis_client,
        redis_key_prefix=settings.redis_key_prefix,
    )
    oauth_service = MCPOAuthService(
        flow_manager=flow_state_manager,
        token_service_instance=token_service,
        registry_app_name=settings.registry_app_name,
        base_redirect_url=settings.registry_client_url,
        encryption_key=settings.encryption_key,
    )
    cfg = HeaderBuildConfig(
        registry_app_name=settings.registry_app_name,
        redis_key_prefix=settings.redis_key_prefix,
        jwt_signing_config=settings.jwt_signing_config,
        encryption_key=settings.encryption_key,
    )
    return make_mcp_headers_provider(
        oauth_service=oauth_service,
        cfg=cfg,
        scope_resolver=lambda ctx: list(ctx.get("scopes") or []),
        redis_client=redis_client,
        interactive=False,
    )


async def _run_scheduler_loop(
    stop_event: asyncio.Event,
    runner: WorkflowRunner,
    schedule_repository: WorkflowScheduleRepository,
) -> None:
    """Main polling loop: claim → spawn task → continue, or sleep until next deadline.

    On claim success, immediately loops back to try claiming another (greedy).
    On claim failure, uses adaptive sleep capped at max_sleep_seconds to avoid
    busy-waiting. On SIGTERM/SIGINT the stop_event breaks the loop and the
    finally block drains all in-flight tasks before returning.
    """
    in_flight: set[asyncio.Task[None]] = set()
    # Per-pod concurrency gate: at most N workflows execute simultaneously
    semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
    try:
        while not stop_event.is_set():
            # Bound claimed + running work per pod. Without this guard, the greedy
            # claim loop can lock every due schedule while executions queue behind
            # the semaphore, starving peer workers and creating unbounded heartbeats.
            await _wait_for_claim_capacity(stop_event, in_flight, settings.max_claimed_runs)
            if stop_event.is_set():
                break

            # Step 1: Try to atomically claim the most urgent due schedule
            schedule = await schedule_repository.claim_due(settings.lease_duration_seconds)
            if schedule is not None:
                # Claimed — fire off execution in background, then immediately
                # loop back to claim the next one (greedy: drain all due work)
                task = asyncio.create_task(
                    run_bounded(
                        schedule,
                        semaphore,
                        runner,
                        schedule_repository,
                        lease_seconds=settings.lease_duration_seconds,
                    )
                )
                in_flight.add(task)
                task.add_done_callback(lambda completed: _handle_task_done(completed, in_flight))
                continue

            # Step 2: Nothing due — find the nearest future deadline and sleep
            # until then, capped at max_sleep_seconds so newly created/toggled
            # schedules are discovered within a bounded window
            next_due = await schedule_repository.peek_next_deadline()
            sleep_seconds = calculate_sleep_seconds(
                next_due=next_due,
                now=datetime.now(UTC),
                max_sleep_seconds=settings.max_sleep_seconds,
            )
            logger.debug("No due workflow schedule; sleeping %.3fs", sleep_seconds)
            # wait_for on stop_event: sleep is interruptible by SIGTERM/SIGINT
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)
            except TimeoutError:
                # Expected: slept the full interval without a stop signal, so loop and re-poll.
                pass
    finally:
        # Graceful shutdown: wait for all in-flight executions to finish
        await asyncio.gather(*in_flight, return_exceptions=True)


async def main() -> None:
    """Worker entrypoint: init shared resources → run scheduler loop → graceful shutdown."""
    _initialize_telemetry()
    logger.info("Starting workflow worker")

    await init_mongodb(settings.mongo_config)
    schedule_repository = WorkflowScheduleRepository(MongoDB.get_database())
    redis_client = create_redis_client(settings.redis_config)
    # DirectiveQueue:Cross-pod control signal queue (pause/resume/cancel) backed by Redis
    directive_queue = DirectiveQueue()
    # Global cancellation manager: runner checks for cancel directives during execution
    set_cancellation_manager(MongoBackedCancellationManager(directive_queue=directive_queue))

    # read=None: workflow runs can be long-lived (LLM inference, external API calls)
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0))
    azure_client_cache = AzureFoundryClientCache(encryption_key=settings.encryption_key)
    runner = _build_runner(directive_queue, redis_client, http_client, azure_client_cache)

    # -- Register OS signals for graceful shutdown --
    # get_running_loop: obtain the event loop created by asyncio.run()
    # add_signal_handler: on SIGTERM (Docker/K8s stop) or SIGINT (Ctrl+C),
    # set stop_event so the main loop exits and drains all in-flight tasks
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await _run_scheduler_loop(stop_event, runner, schedule_repository)
    finally:
        await http_client.aclose()
        await azure_client_cache.close()
        close_redis_client(redis_client)
        await close_mongodb()
        _shutdown_telemetry_safe()


if __name__ == "__main__":
    asyncio.run(main())
