import asyncio
import logging
from datetime import UTC, datetime

from beanie import PydanticObjectId

from registry_pkgs.models import WorkflowDefinition, WorkflowRun, WorkflowSchedule
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.types import UserContextDict
from registry_pkgs.workflows.helpers import extract_user_text
from registry_pkgs.workflows.runner import WorkflowRunner
from registry_pkgs.workflows.schedule_repository import WorkflowScheduleRepository
from registry_pkgs.workflows.scheduling import calculate_next_run_at

logger = logging.getLogger(__name__)

_MINIMUM_HEARTBEAT_INTERVAL_SECONDS = 1.0


async def _renew_lease(
    repository: WorkflowScheduleRepository,
    schedule_id: PydanticObjectId,
    lease_token: str,
    lease_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Heartbeat loop that extends locked_until every lease/3 seconds.

    Runs outside the semaphore so the lease stays alive while waiting for
    concurrency capacity. Stops immediately when the fencing token no longer
    matches (matched_count == 0), meaning another worker took over.
    """
    interval = max(_MINIMUM_HEARTBEAT_INTERVAL_SECONDS, lease_seconds / 3)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            continue
        except TimeoutError:
            # Expected: no stop signal within the interval, so fall through and renew the lease.
            pass
        try:
            matched = await repository.renew_claim(schedule_id, lease_token, lease_seconds)
        except Exception:
            logger.exception("Failed to renew lease for workflow schedule %s", schedule_id)
            return
        if not matched:
            logger.warning("Lease lost while renewing workflow schedule %s", schedule_id)
            return


async def run_bounded(
    schedule: WorkflowSchedule,
    semaphore: asyncio.Semaphore,
    runner: WorkflowRunner,
    repository: WorkflowScheduleRepository,
    lease_seconds: int = 300,
) -> None:
    """Entry point for executing a claimed schedule with concurrency control.

    Starts the heartbeat BEFORE acquiring the semaphore so the lease stays
    alive while queued for capacity. The semaphore gates actual execution;
    the heartbeat is stopped in finally to guarantee cleanup.
    """
    if not schedule.lease_token:
        logger.error("Claimed workflow schedule %s has no lease token", schedule.id)
        return
    stop_event = asyncio.Event()
    heartbeat = asyncio.create_task(
        _renew_lease(repository, schedule.id, schedule.lease_token, lease_seconds, stop_event),
    )
    try:
        async with semaphore:
            await _execute_schedule(schedule, runner, repository)
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
) -> WorkflowRun | None:
    """Atomically advance next_run_at and insert a WorkflowRun in one transaction.

    The fenced update also checks cron_expression + timezone haven't changed since
    claim, preventing a stale next_run_at from being written if the user updated the
    schedule concurrently. Returns None if the fencing check fails.
    """
    next_run_at = calculate_next_run_at(claimed.cron_expression, claimed.timezone)
    run = WorkflowRun(
        workflow_definition_id=definition.id,
        workflow_version=definition.version,
        status=WorkflowRunStatus.PENDING,
        trigger_source="schedule",
        initial_input=claimed.initial_input,
        definition_snapshot=definition.model_dump(mode="json"),
        triggering_user_id=str(claimed.created_by),
    )
    inserted = await repository.advance_and_insert_run(claimed, run, next_run_at)
    return run if inserted else None


async def _prepare_scheduled_run(
    schedule: WorkflowSchedule,
    repository: WorkflowScheduleRepository,
) -> tuple[WorkflowSchedule, WorkflowDefinition, WorkflowRun] | None:
    """Validate the claim, load the workflow, and create the run record.

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
    run = await _create_scheduled_run(claimed, definition, repository)
    if run is None:
        logger.info("Schedule %s was modified during claim; skipping this cycle", claimed.id)
        return None
    return claimed, definition, run


async def _mark_run_failed(run: WorkflowRun, exc: Exception) -> None:
    """Best-effort persistence of FAILED status when runner.run() throws."""
    try:
        persisted_run = await WorkflowRun.get(run.id)
        failed_run = persisted_run or run
        failed_run.status = WorkflowRunStatus.FAILED
        failed_run.error_summary = str(exc)
        failed_run.finished_at = datetime.now(UTC)
        await failed_run.save()
    except Exception:
        logger.exception("Failed to persist failure status for workflow run %s", run.id)


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
) -> None:
    """Orchestrate the full lifecycle: prepare → run → finish.

    If _prepare returns None (workflow gone, lease lost, etc.) exits silently
    without calling _finish_schedule — no run was created, nothing to clean up.
    """
    prepared = await _prepare_scheduled_run(schedule, repository)
    if prepared is None:
        return
    claimed, definition, run = prepared
    final_status = WorkflowRunStatus.FAILED
    try:
        updated_run, _ = await runner.run(
            definition_id=str(definition.id),
            user_text=extract_user_text(claimed.initial_input),
            auth_context=_scheduled_run_auth_context(claimed.created_by),
            existing_run_id=str(run.id),
        )
        final_status = updated_run.status
    except Exception as exc:
        logger.exception("Scheduled workflow execution failed for schedule %s", schedule.id)
        await _mark_run_failed(run, exc)
    finally:
        await _finish_schedule(schedule, run, final_status, repository)
