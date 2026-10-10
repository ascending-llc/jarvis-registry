"""Run-level leasing for ``WorkflowRun``: ownership, fenced writes, shutdown finalize, reaper.

Built on the lease primitives in ``registry_pkgs.database.leased_job`` and the guarded
``RunStateWriter``. A ``WorkflowRun`` is born already leased by the pod that will execute
it and stays leased while ``PENDING``/``RUNNING``/``PAUSED``; the lease is released on entering
``AWAITING_APPROVAL`` (execution is torn down, ``continue_run`` re-acquires) or any terminal status.

Shared by the registry and the workflow-worker:

* ``workflow_run_lease_repository`` — the fenced read/write surface for ``WorkflowRun``.
* ``LeasedRunStateWriter`` — ``RunStateWriter`` + a ``lease_token`` fence on every write, and it
  clears the lease fields when the run reaches ``AWAITING_APPROVAL`` or a terminal status.
* ``fail_open_node_runs`` — bulk-fail a run's still-open ``NodeRun``s.
* ``execute_leased_run`` — run an executor coroutine coupled to the lease heartbeat, finalizing the
  run on process shutdown and on an error that escaped the runner.
* ``reap_expired_runs`` — the periodic janitor: finalize runs whose executor died.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from beanie import PydanticObjectId

from registry_pkgs.database.leased_job import (
    SNAKE_CASE_LEASE_FIELDS,
    Lease,
    LeasedRepository,
    LeaseLostError,
    LeaseSpec,
    run_with_lease,
)
from registry_pkgs.models.enums import NodeRunStatus, WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowRun
from registry_pkgs.workflows.run_repository import (
    TERMINAL_RUN_STATUSES,
    RunStateWriter,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Statuses under which a WorkflowRun holds a lease (a live coroutine owns it).
LEASED_RUN_STATUSES: frozenset[WorkflowRunStatus] = frozenset(
    {WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED}
)
# Statuses that release the lease: a terminal outcome, or AWAITING_APPROVAL (execution torn down).
_RELEASING_RUN_STATUSES: frozenset[WorkflowRunStatus] = TERMINAL_RUN_STATUSES | {WorkflowRunStatus.AWAITING_APPROVAL}

# NodeRun statuses that are still "open" and must be failed when the run is finalized out-of-band.
_OPEN_NODE_RUN_STATUSES = (
    NodeRunStatus.PENDING.value,
    NodeRunStatus.RUNNING.value,
    NodeRunStatus.AWAITING_APPROVAL.value,
)

_REAP_LEASE_EXPIRED = "Executor lost: lease expired"
_REAP_LEGACY = "Executor lost: run predates run leasing"

WORKFLOW_RUN_LEASE: LeaseSpec[WorkflowRun] = LeaseSpec(
    document=WorkflowRun,
    fields=SNAKE_CASE_LEASE_FIELDS,
    status_field="status",
    leased_statuses=frozenset(status.value for status in LEASED_RUN_STATUSES),
    duration=timedelta(minutes=2),
    heartbeat_interval=timedelta(seconds=30),
)
workflow_run_lease_repository: LeasedRepository[WorkflowRun] = LeasedRepository(WORKFLOW_RUN_LEASE)


class LeasedRunStateWriter(RunStateWriter):
    """``RunStateWriter`` fenced on the lease token, releasing the lease on exit statuses.

    Every write additionally matches ``lease_token == lease.token``, so the instant another pod
    re-claims (new token) or the reaper finalizes the run, this writer's updates no-op. When a write
    moves the run to ``AWAITING_APPROVAL`` or a terminal status, the lease fields are cleared in the
    same update. The executor-field assertion inherited from ``RunStateWriter`` runs on the caller's
    fields, before the lease-clear fields are added.
    """

    def __init__(self, lease: Lease) -> None:
        super().__init__(lease.doc_id)
        self._lease = lease

    def _extra_filter(self) -> dict[str, Any]:
        return {SNAKE_CASE_LEASE_FIELDS.token: self._lease.token}

    def _extra_set(self, set_fields: dict[str, Any]) -> dict[str, Any]:
        status = set_fields.get("status")
        if status is None:
            return {}
        status = WorkflowRunStatus(status.value if isinstance(status, WorkflowRunStatus) else status)
        if status not in _RELEASING_RUN_STATUSES:
            return {}
        f = SNAKE_CASE_LEASE_FIELDS
        return {f.owner: None, f.token: None, f.expires_at: None, f.updated_at: datetime.now(UTC)}


async def fail_open_node_runs(run_id: PydanticObjectId, error: str) -> int:
    """Fail a run's still-open ``NodeRun``s (PENDING/RUNNING/AWAITING_APPROVAL) → FAILED. Returns count.

    One ``update_many``. Shared by the reaper, shutdown finalization and the worker. It also fails
    ``AWAITING_APPROVAL`` node runs, so a human-gated node left behind by a dead executor is not
    stuck open.
    """
    result = await NodeRun.get_pymongo_collection().update_many(
        {"workflow_run_id": run_id, "status": {"$in": list(_OPEN_NODE_RUN_STATUSES)}},
        {"$set": {"status": NodeRunStatus.FAILED.value, "error": error, "finished_at": datetime.now(UTC)}},
    )
    return result.modified_count


async def execute_leased_run(
    lease: Lease,
    coro: Any,
    *,
    interrupted_reason: str,
) -> T | None:
    """Run ``coro`` under the lease heartbeat, finalizing the run on shutdown or escaped error.

    Returns the coroutine's result, or ``None`` when the lease was lost. The caller reads the final
    run status from the returned run.

    Outcomes:
    * ``LeaseLostError`` (heartbeat self-fenced out): reload the run; if it is no longer in a leased
      status it released its own lease (e.g. entered AWAITING_APPROVAL) — ``debug``; otherwise
      ``warning``. Write nothing — whoever holds the lease now owns finalization.
    * Cancellation (process shutdown only): ``run_with_lease`` runs ``on_cancelled`` first (still
      holding the lease, before the execution is cancelled) to write FAILED + release; after the
      execution has been cancelled and awaited, if that write matched, fail the open NodeRuns. Doing
      it after the execution has stopped means no wrapper NodeRun write can reopen a node. Re-raise.
    * Any other exception: ``transition(FAILED, "Execution error: ...", release=True)`` then re-raise.
      The token fence makes this a no-op when the runner already wrote a terminal status.
    """
    cancel_write_matched = False

    async def on_cancelled() -> None:
        nonlocal cancel_write_matched
        cancel_write_matched = await workflow_run_lease_repository.transition(
            lease,
            {
                "status": WorkflowRunStatus.FAILED.value,
                "error_summary": interrupted_reason,
                "finished_at": datetime.now(UTC),
            },
            release=True,
        )

    try:
        return await run_with_lease(workflow_run_lease_repository, lease, coro, on_cancelled=on_cancelled)
    except LeaseLostError:
        run = await WorkflowRun.get(lease.doc_id)
        status = run.status if run is not None else None
        if status is None or status not in LEASED_RUN_STATUSES:
            logger.debug("[run=%s] lease released by the run itself (status=%s); no finalize", lease.doc_id, status)
        else:
            logger.warning("[run=%s] lease lost while still %s; another owner will finalize", lease.doc_id, status)
        return None
    except asyncio.CancelledError:
        if cancel_write_matched:
            await fail_open_node_runs(lease.doc_id, interrupted_reason)
        raise
    except Exception as exc:
        await workflow_run_lease_repository.transition(
            lease,
            {
                "status": WorkflowRunStatus.FAILED.value,
                "error_summary": f"Execution error: {exc}",
                "finished_at": datetime.now(UTC),
            },
            release=True,
        )
        raise


async def reap_expired_runs(*, legacy_cutover_age: timedelta) -> int:
    """Finalize runs whose executor died. Returns how many runs were reaped.

    Pass 1 — expired leases: repeatedly ``reap_one`` a leased run whose ``lease_expires_at`` has
    passed, writing FAILED and clearing the lease, then fail its open NodeRuns.

    Pass 2 — legacy runs with no lease fields, started before this release: finalize any
    PENDING/RUNNING/PAUSED run with no ``lease_token`` older than ``legacy_cutover_age``. Kept
    permanently; it costs one indexed query per tick.

    Both passes write FAILED (never CANCELLED) so ``send_retry`` / ``rerun_single_node`` stay available.
    Each reap is a single atomic ``find_one_and_update``, so concurrent pods never finalize a run twice.

    NodeRuns are failed only *after* the run has been atomically finalized, never before. The run
    finalization is the point at which the run is known to be dead; failing its NodeRuns first would
    risk failing the nodes of a run that is merely slow to heartbeat and renews its lease right after
    we observed it expired. The cost of this ordering is a narrow crash window (process dies between
    finalizing the run and failing its NodeRuns) that can leave a FAILED run with still-open NodeRuns;
    that is a display-only inconsistency on an already-terminal run and is preferred over corrupting a
    live run's nodes.
    """
    reaped = 0

    while True:
        run = await workflow_run_lease_repository.reap_one(
            extra_filter={},
            set_fields={
                "status": WorkflowRunStatus.FAILED.value,
                "error_summary": _REAP_LEASE_EXPIRED,
                "finished_at": datetime.now(UTC),
            },
        )
        if run is None:
            break
        await fail_open_node_runs(run.id, _REAP_LEASE_EXPIRED)
        logger.warning("[run=%s] reaped — %s", run.id, _REAP_LEASE_EXPIRED)
        reaped += 1

    collection = WorkflowRun.get_pymongo_collection()
    leased_values = [status.value for status in LEASED_RUN_STATUSES]
    while True:
        cutoff = datetime.now(UTC) - legacy_cutover_age
        document = await collection.find_one_and_update(
            {
                "status": {"$in": leased_values},
                SNAKE_CASE_LEASE_FIELDS.token: {"$exists": False},
                "started_at": {"$lte": cutoff},
            },
            {
                "$set": {
                    "status": WorkflowRunStatus.FAILED.value,
                    "error_summary": _REAP_LEGACY,
                    "finished_at": datetime.now(UTC),
                }
            },
        )
        if document is None:
            break
        await fail_open_node_runs(document["_id"], _REAP_LEGACY)
        logger.warning("[run=%s] reaped — %s", document["_id"], _REAP_LEGACY)
        reaped += 1

    return reaped
