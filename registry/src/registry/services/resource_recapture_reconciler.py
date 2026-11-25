"""Leader-leased reconciler that recaptures servers whose concrete resource list has no capture proof.

A concrete resource list is shown only with a proof that it was captured with the server's current shared
credentials (``mcp_resource_capture``). Lists stored before proofs existed have none, so migration m0006 marks
those servers with ``resourceRecapturePendingAt``. One pod at a time refreshes each marked server through the
normal capabilities refresh, which signs the new list, then clears the marker. Without this, every legacy list
would stay hidden until someone refreshed its server by hand.

The marker is Registry-owned and written only with raw pymongo; Beanie's state-managed saves never touch it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from registry.services.embedding_maintenance_watcher import EmbeddingMaintenanceWatcher
from registry.utils.concurrency import run_bounded
from registry_pkgs.database.leased_job import Lease, LeasedJobRunner, LeasedRepository
from registry_pkgs.models import ExtendedMCPServer, LeaderLease
from registry_pkgs.models.leader_lease import (
    LEADER_LEASE,
    ensure_leader_document,
    leader_claimable_filter,
    release_leader,
)
from registry_pkgs.models.mcp_resource_capture import resource_capture_revision, uses_shared_credentials

if TYPE_CHECKING:
    from registry.services.server_service import ServerServiceV1

logger = logging.getLogger(__name__)

RESOURCE_RECAPTURE_PENDING_AT = "resourceRecapturePendingAt"
RESOURCE_RECAPTURE_ATTEMPTS = "resourceRecaptureAttempts"

_LEASE_NAME = "resource-recapture-reconciler"
_POLL_INTERVAL = timedelta(seconds=30)
_PASS_INTERVAL = timedelta(seconds=60)
# Each recapture is a live downstream call; keep batches small so one pass stays well inside the lease.
_BATCH = 20
_CONCURRENCY = 3
_BACKOFF_BASE = timedelta(minutes=5)
_BACKOFF_MAX = timedelta(hours=6)
# After this many failed recaptures the marker is dropped; the list stays hidden until a manual refresh.
_MAX_ATTEMPTS = 6
_PROJECTION = {
    "config": 1,
    RESOURCE_RECAPTURE_PENDING_AT: 1,
    RESOURCE_RECAPTURE_ATTEMPTS: 1,
}


def needs_resource_recapture(config: dict[str, Any] | None) -> bool:
    """A shared-credential server holding a concrete list that no valid proof signs."""
    config = config or {}
    return bool(config.get("resources")) and uses_shared_credentials(config) and not resource_capture_revision(config)


class ResourceRecaptureReconciler:
    """App-scoped background loop: recapture servers queued by m0006, one pod at a time."""

    def __init__(
        self,
        *,
        server_service: ServerServiceV1,
        embedding_maintenance_watcher: EmbeddingMaintenanceWatcher | None = None,
        lease_owner: str | None = None,
    ) -> None:
        self._server_service = server_service
        self._watcher = embedding_maintenance_watcher
        self._owner = lease_owner or f"registry-{uuid4()}"
        self._repo: LeasedRepository[LeaderLease] = LeasedRepository(LEADER_LEASE)
        self._runner: LeasedJobRunner[LeaderLease] = LeasedJobRunner(
            name=_LEASE_NAME,
            claim=self._claim,
            execute=self._execute,
            repository=self._repo,
            poll_interval=_POLL_INTERVAL,
            owner=self._owner,
        )

    async def start(self) -> None:
        await ensure_leader_document(_LEASE_NAME)
        await self._runner.start()

    async def shutdown(self) -> None:
        await self._runner.shutdown()

    async def _claim(self, owner: str) -> tuple[LeaderLease, Lease] | None:
        return await self._repo.claim(
            owner=owner, claimable_filter=leader_claimable_filter(_LEASE_NAME, datetime.now(UTC))
        )

    async def _execute(self, _lease_doc: LeaderLease, lease: Lease) -> None:
        """Run one pass, then release the lease; a full batch resumes at once on the next poll."""
        full_batch = False
        try:
            full_batch = await self._run_pass()
        finally:
            await release_leader(self._repo, lease, interval=timedelta(0) if full_batch else _PASS_INTERVAL)

    async def _run_pass(self) -> bool:
        """Recapture one batch of due servers. Return whether the batch was full."""
        if self._watcher is not None and self._watcher.is_active():
            # A capabilities refresh rewrites vectors, which a reindex gates.
            logger.debug("Resource-recapture reconciler: reindex active; skipping pass")
            return False

        now = datetime.now(UTC)
        collection = ExtendedMCPServer.get_pymongo_collection()
        cursor = (
            collection.find({RESOURCE_RECAPTURE_PENDING_AT: {"$lte": now}}, _PROJECTION)
            .sort(RESOURCE_RECAPTURE_PENDING_AT, 1)
            .limit(_BATCH)
        )
        docs = await cursor.to_list(length=_BATCH)
        if not docs:
            return False

        results = await run_bounded(docs, self._recapture, limit=_CONCURRENCY)
        done = failed = 0
        for result in results:
            if result.ok and result.result:
                done += 1
                continue
            failed += 1
            if not result.ok:
                logger.warning(
                    "Resource-recapture reconciler: refresh failed for server %s",
                    result.item["_id"],
                    exc_info=result.exc_info,
                )
            await self._requeue(result.item, now)
        logger.info("Resource-recapture reconciler pass: recaptured=%d failed=%d", done, failed)
        return len(docs) == _BATCH

    async def _recapture(self, doc: dict[str, Any]) -> bool:
        """Refresh one server unless it no longer needs it; clear its marker when its list is signed."""
        if needs_resource_recapture(doc.get("config")):
            result = await self._server_service.refresh_server_capabilities(str(doc["_id"]))
            server = result.get("server")
            if result.get("status") != "success" or needs_resource_recapture(getattr(server, "config", None)):
                return False
        await self._clear(doc)
        return True

    @staticmethod
    async def _clear(doc: dict[str, Any]) -> None:
        """Compare-and-clear on the loaded marker, so a newer queueing is never dropped."""
        await ExtendedMCPServer.get_pymongo_collection().update_one(
            {"_id": doc["_id"], RESOURCE_RECAPTURE_PENDING_AT: doc[RESOURCE_RECAPTURE_PENDING_AT]},
            {"$unset": {RESOURCE_RECAPTURE_PENDING_AT: "", RESOURCE_RECAPTURE_ATTEMPTS: ""}},
        )

    async def _requeue(self, doc: dict[str, Any], now: datetime) -> None:
        """Back off exponentially; give up after _MAX_ATTEMPTS, leaving the list for a manual refresh."""
        attempts = int(doc.get(RESOURCE_RECAPTURE_ATTEMPTS) or 0) + 1
        if attempts >= _MAX_ATTEMPTS:
            logger.warning(
                "Resource-recapture reconciler: giving up on server %s after %d attempts; "
                "its concrete resources stay hidden until a manual capabilities refresh",
                doc["_id"],
                attempts,
            )
            await self._clear(doc)
            return
        delay = min(_BACKOFF_BASE * (2 ** (attempts - 1)), _BACKOFF_MAX)
        await ExtendedMCPServer.get_pymongo_collection().update_one(
            {"_id": doc["_id"], RESOURCE_RECAPTURE_PENDING_AT: doc[RESOURCE_RECAPTURE_PENDING_AT]},
            {"$set": {RESOURCE_RECAPTURE_PENDING_AT: now + delay, RESOURCE_RECAPTURE_ATTEMPTS: attempts}},
        )
