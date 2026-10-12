import asyncio
import logging
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId

from registry_pkgs.database.leased_job import Lease, interruptible_sleep
from registry_pkgs.models import WorkflowDefinition, WorkflowRun, WorkflowSchedule
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.types import UserContextDict
from registry_pkgs.workflows.helpers import extract_user_text
from registry_pkgs.workflows.run_lease import (
    LeasedRunStateWriter,
    execute_leased_run,
    workflow_run_lease_repository,
)
from registry_pkgs.workflows.runner import WorkflowRunner
from registry_pkgs.workflows.schedule_repository import WorkflowScheduleRepository
from registry_pkgs.workflows.scheduling import calculate_next_run_at

logger = logging.getLogger(__name__)

_MINIMUM_HEARTBEAT_INTERVAL_SECONDS = 1.0
_WORKER_SHUTDOWN_REASON = "Interrupted by workflow-worker shutdown; retry the run"


async def _renew_lease(
    repository: WorkflowScheduleRepository,
    schedule_id: PydanticObjectId,
    lease_token: str,
    lease_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Heartbeat loop that extends locked_until every lease/3 seconds.

    Runs outside the semaphore so the lease stays alive while waiting for concurrency capacity.

    Tolerant of transient Mongo errors: a failed ``renew_claim`` is logged and retried next
    interval rather than abandoning the claim. Only two things end the loop early:
      * the fencing token no longer matches (``matched == 0``): another worker took over;
      * ``last_renewed_until`` has passed: every renewal since the last success failed long enough
        that the schedule lock has actually lapsed, so the next occurrence may overlap this run.

    Either way the run keeps executing — losing the *schedule* claim only risks one overlapping
    occurrence (nothing enforces non-overlap); the run itself is protected by its own run lease.
    """
    interval = max(_MINIMUM_HEARTBEAT_INTERVAL_SECONDS, lease_seconds / 3)
    last_renewed_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    while not stop_event.is_set():
        if await interruptible_sleep(stop_event, interval):
            return
        now = datetime.now(UTC)
        if now >= last_renewed_until:
            logger.warning(
                "Schedule claim %s lapsed while renewing; the next occurrence may overlap this run",
                schedule_id,
            )
            return
        try:
            matched = await repository.renew_claim(schedule_id, lease_token, lease_seconds)
        except Exception:
            logger.warning("Failed to renew lease for workflow schedule %s; retrying", schedule_id, exc_info=True)
            continue
        if not matched:
            logger.warning("Lease lost while renewing workflow schedule %s", schedule_id)
            return
        last_renewed_until = now + timedelta(seconds=lease_seconds)


async def run_bounded(
    schedule: WorkflowSchedule,
    semaphore: asyncio.Semaphore,
    runner: WorkflowRunner,
    repository: WorkflowScheduleRepository,
    lease_owner: str,
    executing: set[asyncio.Task[None]],
    lease_seconds: int = 300,
) -> None:
    """Entry point for executing a claimed schedule with concurrency control.

    Starts the heartbeat BEFORE acquiring the semaphore so the lease stays alive while queued for
    capacity. The semaphore gates actual execution; the heartbeat is stopped in finally to
    guarantee cleanup.

    Registers this task in ``executing`` only once past the semaphore, so shutdown drain can tell a
    running task from one still queued. A task cancelled *while queued* releases its schedule claim
    (``next_run_at`` untouched) so a peer worker picks the occurrence up immediately.
    """
    if not schedule.lease_token:
        logger.error("Claimed workflow schedule %s has no lease token", schedule.id)
        return
    stop_event = asyncio.Event()
    heartbeat = asyncio.create_task(
        _renew_lease(repository, schedule.id, schedule.lease_token, lease_seconds, stop_event),
    )
    entered = False
    current = asyncio.current_task()
    try:
        async with semaphore:
            entered = True
            if current is not None:
                executing.add(current)
            try:
                await _execute_schedule(schedule, runner, repository, lease_owner)
            finally:
                if current is not None:
                    executing.discard(current)
    except asyncio.CancelledError:
        if not entered:
            # Cancelled while still queued on the semaphore (never executed): release the claim so
            # another worker can run this occurrence now instead of waiting for the lock to expire.
            await repository.release_claim(schedule.id, schedule.lease_token)
        raise
    finally:
        stop_event.set()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def _load_current_claim(
    schedule: WorkflowSchedule,
    repository: WorkflowScheduleRepository,
) -> WorkflowSchedule | None:
    """Re-read the schedule with lease_token + enabled check before execution.

    Guards against the window between claim and execution where the schedule
    could have been disabled or its lease stolen by another worker.
    """
    return await repository.load_claim(schedule.id, schedule.lease_token)


async def _create_scheduled_run(
    claimed: WorkflowSchedule,
    definition: WorkflowDefinition,
    repository: WorkflowScheduleRepository,
    lease_owner: str,
) -> tuple[WorkflowRun, Lease] | None:
    """Atomically advance next_run_at and insert a run that is born already leased by this worker.

    The run is minted with lease fields (owner/token/expires_at) so it has an owner the instant it
    is inserted — AS-1909's reaper never treats a worker-executed run as an unowned orphan. The
    fenced update also checks cron_expression + timezone haven't changed since claim, preventing a
    stale next_run_at from being written if the user updated the schedule concurrently.

    Returns ``(run, lease)``, or None if the fencing check fails.
    """
    next_run_at = calculate_next_run_at(claimed.cron_expression, claimed.timezone)
    run_id = PydanticObjectId()
    lease = workflow_run_lease_repository.new_lease(lease_owner, doc_id=run_id)
    run = WorkflowRun(
        id=run_id,
        **workflow_run_lease_repository.insert_fields(lease),
        workflow_definition_id=PydanticObjectId(definition.id),
        workflow_version=definition.version,
        status=WorkflowRunStatus.PENDING,
        trigger_source="schedule",
        initial_input=claimed.initial_input,
        definition_snapshot=definition.model_dump(mode="json"),
        triggering_user_id=str(claimed.created_by),
    )
    inserted = await repository.advance_and_insert_run(claimed, run, next_run_at)
    return (run, lease) if inserted else None


async def _prepare_scheduled_run(
    schedule: WorkflowSchedule,
    repository: WorkflowScheduleRepository,
    lease_owner: str,
) -> tuple[WorkflowSchedule, WorkflowDefinition, WorkflowRun, Lease] | None:
    """Validate the claim, load the workflow, and create the leased run record.

    Returns None (skip, no error) in three cases:
    1. Lease was lost or schedule disabled between claim and execution
    2. Workflow definition is missing or disabled — auto-disables the schedule
    3. Schedule was concurrently modified (cron/tz changed) — skip this cycle
    """
    claimed = await _load_current_claim(schedule, repository)
    if claimed is None:
        return None
    definition = await WorkflowDefinition.get(claimed.workflow_definition_id)
    if definition is None or not definition.enabled:
        await repository.disable_claim(claimed.id, claimed.lease_token)
        logger.warning(
            "Schedule %s skipped: workflow %s is missing or disabled",
            claimed.id,
            claimed.workflow_definition_id,
        )
        return None
    created = await _create_scheduled_run(claimed, definition, repository, lease_owner)
    if created is None:
        logger.info("Schedule %s was modified during claim; skipping this cycle", claimed.id)
        return None
    run, lease = created
    return claimed, definition, run, lease


async def _read_run_status(run_id: PydanticObjectId) -> WorkflowRunStatus:
    """Projected read of a run's current status, for when execute_leased_run reports no result.

    (lease lost or a write rejected: whoever holds the lease wrote the final status.) Defaults to
    FAILED if the run has vanished.
    """
    document = await WorkflowRun.get_pymongo_collection().find_one({"_id": run_id}, {"status": 1})
    if document is None:
        return WorkflowRunStatus.FAILED
    return WorkflowRunStatus(document["status"])


async def _finish_schedule(
    schedule: WorkflowSchedule,
    run: WorkflowRun | None,
    final_status: WorkflowRunStatus,
    repository: WorkflowScheduleRepository,
) -> None:
    """Release the lease and record last_run metadata, fenced by lease_token.

    If matched_count == 0 the lease was already superseded — log and move on.
    """
    matched = await repository.finish_claim(
        schedule.id,
        schedule.lease_token,
        run.id if run is not None else None,
        final_status,
    )
    if not matched:
        logger.info("Schedule %s was deleted or its lease was superseded before completion", schedule.id)


def _scheduled_run_auth_context(created_by: PydanticObjectId) -> UserContextDict:
    """Build the auth_context a scheduled run presents to mcp_headers_provider.

    There is no live user session for a schedule-triggered run; user_id (the only field
    build_authenticated_headers requires) is the WorkflowSchedule's creator. The remaining
    fields are placeholders — a scheduled run has no scopes/groups to contribute, and the
    provider's OAuth path keys solely off user_id.
    """
    return UserContextDict(
        user_id=str(created_by),
        client_id="workflow-worker",
        username=None,
        groups=[],
        scopes=[],
        auth_method="schedule",
        provider="workflow-worker",
        auth_source="workflow_schedule",
    )


async def _execute_schedule(
    schedule: WorkflowSchedule,
    runner: WorkflowRunner,
    repository: WorkflowScheduleRepository,
    lease_owner: str,
) -> None:
    """Orchestrate the full lifecycle: prepare → run under the run lease → finish.

    If _prepare returns None (workflow gone, lease lost, etc.) exits silently without calling
    _finish_schedule — no run was created, nothing to clean up.

    The run executes under ``execute_leased_run``, which heartbeats the run lease, and on shutdown
    or an escaped error writes FAILED (fenced by the lease) and fails open NodeRuns before
    re-raising — so this function never writes the run's failure itself.
    """
    prepared = await _prepare_scheduled_run(schedule, repository, lease_owner)
    if prepared is None:
        return
    claimed, definition, run, lease = prepared
    final_status = WorkflowRunStatus.FAILED
    try:
        result = await execute_leased_run(
            lease,
            runner.run(
                definition_id=str(definition.id),
                user_text=extract_user_text(claimed.initial_input),
                auth_context=_scheduled_run_auth_context(claimed.created_by),
                existing_run_id=str(run.id),
                run_writer=LeasedRunStateWriter(lease),
            ),
            interrupted_reason=_WORKER_SHUTDOWN_REASON,
        )
        if result is None:
            final_status = await _read_run_status(run.id)
        else:
            updated_run, _ = result
            final_status = updated_run.status
    except Exception:
        # execute_leased_run already wrote FAILED (fenced) before re-raising; just log here.
        logger.exception("Scheduled workflow execution failed for schedule %s", schedule.id)
    finally:
        await _finish_schedule(schedule, run, final_status, repository)
