"""Leader-leased reconciler that heals documents whose Weaviate sync never completed.

Every vector-relevant write stamps ``vectorSyncPendingAt`` in the same commit and fires an immediate
(tracked) sync that clears it on success. If the pod died, the task was cancelled, or the sync failed,
the marker stays set. This reconciler is the durable backstop: on a cluster-wide interval, one pod at
a time re-syncs every still-marked document and clears or re-queues it.

It is built on the shared lease library (``LeasedJobRunner`` + ``LeaderLease``): the lease gives mutual
exclusion across pods, and ``nextRunAt`` (via ``release_leader``) gives the interval, so N pods don't
run the pass N times a minute against the paid embedding provider.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from registry.services.embedding_maintenance_watcher import EmbeddingMaintenanceWatcher
from registry.services.vector_sync_helpers import (
    VectorModel,
    VectorRepository,
    VectorSyncBusyError,
    sync_pending_vectors,
)
from registry.utils.concurrency import run_bounded
from registry_pkgs.database.leased_job import Lease, LeasedJobRunner, LeasedRepository
from registry_pkgs.models import A2AAgent, ExtendedMCPServer, LeaderLease
from registry_pkgs.models.leader_lease import (
    LEADER_LEASE,
    ensure_leader_document,
    leader_claimable_filter,
    release_leader,
)
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

logger = logging.getLogger(__name__)

_LEASE_NAME = "vector-sync-reconciler"
# Poll faster than the pass interval so a just-released lease is re-claimed promptly once due.
_POLL_INTERVAL = timedelta(seconds=30)
_PASS_INTERVAL = timedelta(seconds=60)
# A just-written document's immediate task gets this long before the reconciler also tries it, so the
# two don't race on the common (healthy) path.
_GRACE = timedelta(seconds=60)
_BATCH = 200
_CONCURRENCY = 5
_BACKOFF_BASE = timedelta(seconds=60)
_BACKOFF_MAX = timedelta(hours=6)
# 2**attempts past this already exceeds _BACKOFF_MAX; cap the shift so the timedelta math can't overflow.
_MAX_BACKOFF_SHIFT = 20


class VectorSyncReconciler:
    """App-scoped background loop: heal still-pending vector syncs, one pod at a time.

    A thin wrapper over :class:`LeasedJobRunner`, like the other durable runners. ``claim`` takes the
    leader lease only when it is free and due; ``execute`` runs one pass under the runner's heartbeat
    and always releases the lease (scheduling the next pass) in a ``finally``.
    """

    def __init__(
        self,
        *,
        mcp_server_repo: MCPServerRepository,
        a2a_agent_repo: A2AAgentRepository,
        embedding_maintenance_watcher: EmbeddingMaintenanceWatcher | None = None,
        lease_owner: str | None = None,
    ) -> None:
        self._mcp_server_repo = mcp_server_repo
        self._a2a_agent_repo = a2a_agent_repo
        self._watcher = embedding_maintenance_watcher
        # One owner for the leader lease and the per-entity leases it takes, so logs correlate to the pod.
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
        """Run one pass under the runner's heartbeat, then release and schedule the next pass.

        A full batch means there is a backlog, so release with interval 0: the next poll by any pod
        continues it at once. A pass that raises still releases (with the normal interval), so a failure
        never holds the lease until expiry.
        """
        full_batch = False
        try:
            full_batch = await self._run_pass()
        finally:
            await release_leader(self._repo, lease, interval=timedelta(0) if full_batch else _PASS_INTERVAL)

    async def _run_pass(self) -> bool:
        """Reconcile one batch of servers and one of agents. Return whether either batch was full."""
        if self._watcher is not None and self._watcher.is_active():
            # Vector writes are gated during a reindex, and the reindex sweeps everything anyway.
            logger.debug("Vector-sync reconciler: reindex active; skipping pass")
            return False

        now = datetime.now(UTC)
        mcp_synced, mcp_failed, mcp_full = await self._reconcile(ExtendedMCPServer, self._mcp_server_repo, now)
        a2a_synced, a2a_failed, a2a_full = await self._reconcile(A2AAgent, self._a2a_agent_repo, now)
        if mcp_synced or mcp_failed or a2a_synced or a2a_failed:
            # Remaining = still due now (re-queued failures sit in the future and are not counted).
            mcp_remaining = await self._count_due(ExtendedMCPServer, now)
            a2a_remaining = await self._count_due(A2AAgent, now)
            logger.info(
                "Vector-sync reconciler pass: servers synced=%d failed=%d remaining=%d, "
                "agents synced=%d failed=%d remaining=%d",
                mcp_synced,
                mcp_failed,
                mcp_remaining,
                a2a_synced,
                a2a_failed,
                a2a_remaining,
            )
        return mcp_full or a2a_full

    @staticmethod
    async def _count_due(model: type[VectorModel], now: datetime) -> int:
        return await model.get_pymongo_collection().count_documents({"vectorSyncPendingAt": {"$lte": now - _GRACE}})

    async def _reconcile(
        self, model: type[VectorModel], repo: VectorRepository, now: datetime
    ) -> tuple[int, int, bool]:
        """Re-sync up to one batch of ``model`` documents older than the grace window.

        Returns ``(synced, failed, batch_was_full)``. A future marker (set by a re-queue) is not
        eligible until it is ``_GRACE`` in the past, so a failing document stops blocking newer ones.
        """
        cutoff = now - _GRACE
        docs = (
            await model.find({"vectorSyncPendingAt": {"$lte": cutoff}})
            .sort("+vectorSyncPendingAt")
            .limit(_BATCH)
            .to_list()
        )
        if not docs:
            return 0, 0, False

        results = await run_bounded(
            docs, lambda d: sync_pending_vectors(model, repo, d.id, owner=self._owner), limit=_CONCURRENCY
        )
        synced = failed = 0
        for result in results:
            doc = result.item
            if result.ok:
                synced += int(bool(result.result))
            elif isinstance(result.error, VectorSyncBusyError):
                continue  # Contention is not a provider failure; leave the marker due.
            else:
                failed += 1
                logger.warning(
                    "Vector-sync reconciler: sync failed for %s %s; re-queuing with backoff",
                    model.__name__,
                    doc.id,
                    exc_info=result.exc_info,
                )
                await self._requeue(model, doc, now)
        return synced, failed, len(docs) == _BATCH

    @staticmethod
    async def _requeue(model: type[VectorModel], doc: VectorModel, now: datetime) -> None:
        """Push the marker forward by exponential backoff and bump the attempt counter.

        Compare-and-set on the loaded marker: a newer write (which already reset the counter and moved
        the marker) is never pushed back by this stale re-queue.
        """
        attempts = doc.vectorSyncAttempts or 0
        shift = min(attempts, _MAX_BACKOFF_SHIFT)
        delay = min(_BACKOFF_BASE * (2**shift), _BACKOFF_MAX)
        await model.get_pymongo_collection().update_one(
            {"_id": doc.id, "vectorSyncPendingAt": doc.vectorSyncPendingAt},
            {"$set": {"vectorSyncPendingAt": now + delay}, "$inc": {"vectorSyncAttempts": 1}},
        )
