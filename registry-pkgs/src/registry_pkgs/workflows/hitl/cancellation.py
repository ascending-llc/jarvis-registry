from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agno.exceptions import RunCancelledException
from agno.run.cancellation_management.base import BaseRunCancellationManager
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowDirective
from registry_pkgs.models.workflow import WorkflowRun
from registry_pkgs.workflows.hitl.projections import PendingDirectiveProjection
from registry_pkgs.workflows.run_repository import NON_TERMINAL_RUN_STATUSES

if TYPE_CHECKING:
    from registry_pkgs.workflows.control.queue import DirectiveQueue

logger = logging.getLogger(__name__)


class MongoBackedCancellationManager(BaseRunCancellationManager):
    """Bridge agno cancel signals ⇄ ``WorkflowRun.pending_directive``.

    ``directive_queue`` is optional; when wired, ``acancel_run`` pushes CANCEL
    onto it so the wait-loop wrapper wakes up immediately instead of waiting
    up to 60s for its next Mongo poll.
    """

    def __init__(self, directive_queue: DirectiveQueue | None = None) -> None:
        super().__init__()
        self._directive_queue = directive_queue

    async def aregister_run(self, run_id: str) -> None:
        return None

    async def acancel_run(self, run_id: str) -> bool:
        try:
            oid = PydanticObjectId(run_id)
        except Exception as exc:
            logger.warning("acancel_run: invalid run_id %s: %s", run_id, exc)
            return False
        # Only arm CANCEL on a run that is still non-terminal: once a run has finished,
        # _finalize_cancel must not re-arm a stale CANCEL on top of the terminal state.
        result = await WorkflowRun.get_pymongo_collection().update_one(
            {"_id": oid, "status": {"$in": [status.value for status in NON_TERMINAL_RUN_STATUSES]}},
            {"$set": {"pending_directive": WorkflowDirective.CANCEL.value}},
        )
        modified = result.modified_count == 1
        if modified and self._directive_queue is not None:
            try:
                self._directive_queue.put(run_id, WorkflowDirective.CANCEL)
            except Exception as exc:
                # Queue push failure is non-fatal — Mongo write is the truth source.
                logger.warning("acancel_run: directive_queue.put failed for %s: %s", run_id, exc)
        logger.info("acancel_run: run_id=%s modified=%s", run_id, modified)
        return modified

    async def ais_cancelled(self, run_id: str) -> bool:
        try:
            oid = PydanticObjectId(run_id)
        except Exception as e:
            logger.debug("ais_cancelled: invalid run_id %s: %s", run_id, e)
            return False
        run = await WorkflowRun.find_one(
            WorkflowRun.id == oid,
            projection_model=PendingDirectiveProjection,
        )
        return run is not None and run.pending_directive == WorkflowDirective.CANCEL

    async def araise_if_cancelled(self, run_id: str) -> None:
        if await self.ais_cancelled(run_id):
            raise RunCancelledException(f"Run {run_id} was cancelled")

    async def acleanup_run(self, run_id: str) -> None:
        return None

    async def aget_active_runs(self) -> dict[str, bool]:
        return {}

    def register_run(self, run_id: str) -> None:
        return None

    def cancel_run(self, run_id: str) -> bool:
        raise NotImplementedError("use acancel_run")

    def is_cancelled(self, run_id: str) -> bool:
        raise NotImplementedError("use ais_cancelled")

    def raise_if_cancelled(self, run_id: str) -> None:
        raise NotImplementedError("use araise_if_cancelled")

    def cleanup_run(self, run_id: str) -> None:
        return None

    def get_active_runs(self) -> dict[str, bool]:
        return {}
