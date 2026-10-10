"""Launches registry-executed WorkflowRuns as tracked, leased background tasks.

Replaces the old FastAPI background-task and bare ``asyncio.create_task`` fire-and-forget path that
drove on-demand, retry, rerun, replay and HITL-continue runs. Every launch:

* runs under a lease heartbeat via ``execute_leased_run`` (the run is finalized if its pod dies), and
* is spawned through the shared ``BackgroundTaskTracker`` (cancelled and finalized on shutdown).

The run document is always inserted — already leased — before ``launch_run`` is called, so if the
tracker refuses the spawn (shutdown has begun) the launcher fails that run immediately rather than
leaving it ``PENDING`` for the reaper to flag as "Executor lost".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from beanie import PydanticObjectId

from registry.auth.dependencies import UserContextDict
from registry.services.background_task_tracker import BackgroundTaskTracker
from registry_pkgs.database.leased_job import Lease
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.workflows.run_lease import (
    LeasedRunStateWriter,
    execute_leased_run,
    workflow_run_lease_repository,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from registry_pkgs.workflows.runner import WorkflowRunner

logger = logging.getLogger(__name__)

_REGISTRY_SHUTDOWN_REASON = "Interrupted by registry shutdown; retry the run"

# Sentinel a ``launch_continue`` ``prepare`` returns to abort the resume (e.g. reauthentication
# failed) without continuing. Distinct from returning ``None``, which is a valid "no auth context".
ABORT_CONTINUE: Any = object()


class WorkflowRunLauncher:
    """Starts leased, tracked executor tasks for new runs and HITL continuations."""

    def __init__(
        self,
        runner: WorkflowRunner,
        lease_owner: str,
        tracker: BackgroundTaskTracker,
    ) -> None:
        self._runner = runner
        self._lease_owner = lease_owner
        self._tracker = tracker

    def new_lease(self, run_id: PydanticObjectId) -> Lease:
        """Mint a lease this pod owns for a run it is about to insert already leased."""
        return workflow_run_lease_repository.new_lease(self._lease_owner, doc_id=run_id)

    async def launch_run(
        self,
        *,
        lease: Lease,
        definition_id: str,
        user_text: str,
        auth_context: UserContextDict | None,
        existing_run_id: str,
        injected_outputs: dict[str, dict[str, Any]] | None = None,
        stop_after_node_id: str | None = None,
        definition_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Spawn a tracked, leased execution of an already-inserted run.

        Raises ``RuntimeError`` (from the tracker) once shutdown has begun; the caller maps it to 503.
        The run is already inserted ``PENDING`` and leased, so on refusal it is failed here instead of
        being left for the reaper.
        """

        async def _job() -> None:
            await execute_leased_run(
                lease,
                self._runner.run(
                    definition_id,
                    user_text,
                    auth_context=auth_context,
                    existing_run_id=existing_run_id,
                    injected_outputs=injected_outputs,
                    stop_after_node_id=stop_after_node_id,
                    definition_snapshot=definition_snapshot,
                    run_writer=LeasedRunStateWriter(lease),
                ),
                interrupted_reason=_REGISTRY_SHUTDOWN_REASON,
            )

        try:
            self._tracker.spawn(_job(), name=f"workflow-run-{existing_run_id}")
        except RuntimeError:
            await workflow_run_lease_repository.transition(
                lease,
                {
                    "status": WorkflowRunStatus.FAILED.value,
                    "error_summary": _REGISTRY_SHUTDOWN_REASON,
                    "finished_at": datetime.now(UTC),
                },
                release=True,
            )
            raise

    async def launch_continue(
        self,
        *,
        run_id: PydanticObjectId,
        prepare: Callable[[], Awaitable[UserContextDict | None]],
    ) -> None:
        """Spawn a tracked, leased HITL continuation.

        ``prepare`` performs the control service's auth refresh + reauth-fail check and returns the
        auth context to resume with, or ``ABORT_CONTINUE`` to stop (reauth failed). The launcher then
        wins the ``AWAITING_APPROVAL`` → ``RUNNING`` transition via ``acquire`` (this replaces the
        runner's own compare-and-set for registry callers); losing the race stops quietly.

        Raises ``RuntimeError`` (from the tracker) once shutdown has begun; the caller maps it to 503.
        """

        async def _job() -> None:
            prepared = await prepare()
            if prepared is ABORT_CONTINUE:
                return
            auth_context = prepared
            lease = await workflow_run_lease_repository.acquire(
                doc_id=run_id,
                owner=self._lease_owner,
                expected_filter={"status": WorkflowRunStatus.AWAITING_APPROVAL.value},
                set_fields={"status": WorkflowRunStatus.RUNNING.value},
            )
            if lease is None:
                logger.info("[run=%s] continue lost the race (no longer AWAITING_APPROVAL)", run_id)
                return
            await execute_leased_run(
                lease,
                self._runner.continue_run(
                    existing_run_id=str(run_id),
                    auth_context=auth_context,
                    run_writer=LeasedRunStateWriter(lease),
                ),
                interrupted_reason=_REGISTRY_SHUTDOWN_REASON,
            )

        self._tracker.spawn(_job(), name=f"workflow-continue-{run_id}")
