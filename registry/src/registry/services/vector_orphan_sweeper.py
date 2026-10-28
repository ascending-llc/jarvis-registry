"""Leader-leased periodic sweep that deletes Weaviate vectors whose Mongo entity no longer exists.

Entity deletes remove vectors best-effort (the route schedules a tracked delete after the Mongo
commit; federation cleanup is best-effort). When that delete is lost, refused or fails, the vectors
outlive their entity and surface in discovery and search. This sweep is the durable backstop: on a
cluster-wide hourly interval, one pod at a time enumerates the entity ids stored in each collection,
diffs them against Mongo, and deletes the orphans.

Built on the same lease library as the reconciler (``LeasedJobRunner`` + ``LeaderLease``): the lease
gives mutual exclusion across pods, and ``nextRunAt`` (via ``release_leader``) gives the interval, so
N pods don't run the pass N times an hour.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from registry.services.embedding_maintenance_watcher import EmbeddingMaintenanceWatcher
from registry.services.vector_sync_helpers import delete_orphan_vectors
from registry_pkgs.database.leased_job import Lease, LeasedJobRunner, LeasedRepository
from registry_pkgs.models import LeaderLease
from registry_pkgs.models.leader_lease import (
    LEADER_LEASE,
    ensure_leader_document,
    leader_claimable_filter,
    release_leader,
)
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

logger = logging.getLogger(__name__)

_LEASE_NAME = "vector-orphan-sweep"
# Poll faster than the sweep interval so a just-released lease is re-claimed promptly once due.
_POLL_INTERVAL = timedelta(minutes=5)
_SWEEP_INTERVAL = timedelta(hours=1)
# While a reindex owns the collections (and its catch-up already deletes removed entities), skip the
# pass and retry soon rather than waiting a full hour.
_REINDEX_RETRY_INTERVAL = timedelta(minutes=5)


class VectorOrphanSweeper:
    """App-scoped background loop: delete orphan vectors, one pod at a time, hourly.

    A thin wrapper over :class:`LeasedJobRunner`, like the reconciler. ``claim`` takes the leader lease
    only when it is free and due; ``execute`` runs one pass under the runner's heartbeat and always
    releases the lease (scheduling the next pass) in a ``finally``.
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
        self._repo: LeasedRepository[LeaderLease] = LeasedRepository(LEADER_LEASE)
        self._runner: LeasedJobRunner[LeaderLease] = LeasedJobRunner(
            name=_LEASE_NAME,
            claim=self._claim,
            execute=self._execute,
            repository=self._repo,
            poll_interval=_POLL_INTERVAL,
            owner=lease_owner,
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

        A pass that raises still releases (with the normal interval), so a failure never holds the
        lease until expiry. The reindex-skip case asks for a shorter retry interval instead.
        """
        interval = _SWEEP_INTERVAL
        try:
            interval = await self._run_pass()
        finally:
            await release_leader(self._repo, lease, interval=interval)

    async def _run_pass(self) -> timedelta:
        """Delete orphans once. Returns the interval to release with (shorter when a reindex is active).

        The vector id sets are read **before** ``delete_orphan_vectors`` reads Mongo, so an entity
        created in between is never a false orphan and an entity deleted in between is correctly swept.
        """
        if self._watcher is not None and self._watcher.is_active():
            # The reindex owns the collections and its catch-up already deletes removed entities.
            logger.debug("Vector orphan sweep: reindex active; skipping pass")
            return _REINDEX_RETRY_INTERVAL

        vector_mcp_ids = await self._mcp_server_repo.alist_entity_ids()
        vector_a2a_ids = await self._a2a_agent_repo.alist_entity_ids()
        mcp_deleted, a2a_deleted = await delete_orphan_vectors(
            self._mcp_server_repo,
            self._a2a_agent_repo,
            vector_mcp_ids=vector_mcp_ids,
            vector_a2a_ids=vector_a2a_ids,
        )
        # Orphans mean a delete path lost a cleanup, so log at warning when anything was deleted.
        log = logger.warning if (mcp_deleted or a2a_deleted) else logger.info
        log("Vector orphan sweep pass: servers deleted=%d, agents deleted=%d", mcp_deleted, a2a_deleted)
        return _SWEEP_INTERVAL
