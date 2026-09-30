"""Execution of a claimed embedding-reindex job using collection generations.

Re-embeds the corpus into a new collection generation (``<Base>_<jobId>``), then commits the
``(model, generation)`` pair with one compare-and-set, in a Mongo transaction that also re-checks
this pod's lease. Never swaps the shared adapter — every pod follows the selection via its own
watcher. On failure nothing is committed; the abandoned generation is reclaimed later by GC.

Every write to the job goes through ``transition_embedding_reindex_job`` (lease-checked), so a pod
whose lease was taken over can neither commit nor overwrite a finished job — it raises
``EmbeddingReindexLeaseLostError`` and stops.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from registry.core.config import Settings
from registry.core.mcp_config import MCPClientConfig
from registry.core.vector_backend import build_backend_config_from_model_source
from registry.services.federation_job_service import FederationJobService
from registry.utils.concurrency import run_bounded
from registry_pkgs.database.embedding_reindex_job_repository import transition_embedding_reindex_job
from registry_pkgs.database.model_gateway_selection_repository import (
    commit_embedding_generation,
    get_model_gateway_selection,
)
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus
from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.vector.client import DatabaseClient, create_database_client
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

logger = logging.getLogger(__name__)

_REINDEX_CONCURRENCY = 5
# Documents pulled from Mongo (and re-embedded) per batch, so peak memory is O(batch) not O(corpus).
_REINDEX_BATCH_SIZE = 200
# Automatic retries before a job is given up on (kept here so the executor's give-up message and the
# runner's exhaustion check share one source; the runner imports it).
_MAX_ATTEMPTS = 3
# A request that passed the write gate just before it closed can still be committing: the longest
# gated path is one full capability fetch (init + tools + resources + prompts, all sequential in
# mcp_client) plus scheduling/poll slack. Derived from the MCP timeouts so it tracks any change to
# them. Measured from startedAt, so a sweep that already ran longer waits for nothing.
_CATCH_UP_SLACK_SECONDS = 10.0
_CATCH_UP_MIN_DELAY = timedelta(
    seconds=MCPClientConfig.INIT_TIMEOUT + 3 * MCPClientConfig.TOOLS_TIMEOUT + _CATCH_UP_SLACK_SECONDS
)
# updatedAt is stamped at save(), possibly before a surrounding transaction commits, and pod clocks
# drift; widen the re-sync window backwards to be safe. Re-syncing extra documents is harmless.
_CATCH_UP_WATERMARK_MARGIN = timedelta(minutes=5)
# Poll cadence for the pre-commit drain wait, and the ceiling before a stuck federation sync fails
# the reindex instead of wedging writes indefinitely.
_CATCH_UP_POLL_SECONDS = 5.0
_FEDERATION_DRAIN_TIMEOUT = timedelta(minutes=10)
_FEDERATION_DRAIN_TIMEOUT_MSG = "Federation sync still active after 10 minutes; embedding model NOT switched"


class EmbeddingReindexLeaseLostError(RuntimeError):
    """The job's RUNNING lease is no longer held by this pod; it must stop all writes for the job."""


class _Superseded(Exception):
    """Internal: a compare-and-set miss inside the commit transaction, used to abort and roll back."""


class _Aborted(Exception):
    """Internal: the job was already terminally handled (FAILED) inside a helper; unwind without commit."""


def _as_utc(value: datetime) -> datetime:
    """Mongo returns datetimes tz-naive (stored as UTC); make one aware before arithmetic."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _drop_collection(client: DatabaseClient, name: str) -> None:
    """Drop a generation collection if it exists, so the sweep starts clean.

    A real drop failure must propagate (not be swallowed): otherwise a resumed job would append to a
    partially-populated collection and commit a dirty generation. A missing collection is a no-op.
    """
    adapter = client.write_adapter  # job-local client has no reindex gate wired
    if not hasattr(adapter, "drop_collection"):
        return
    if hasattr(adapter, "collection_exists") and not adapter.collection_exists(name):
        return
    adapter.drop_collection(name)


async def _reindex_server(repo: MCPServerRepository, server: ExtendedMCPServer, *, is_delete: bool = False) -> None:
    """Sync one server into the fresh generation; raise on failure so run_bounded records it.

    ``is_delete=False`` (sweep) inserts into the empty generation; ``is_delete=True`` (catch-up)
    replaces an entity already swept in, so a changed chunk count drops the stale trailing chunks.
    """
    result = await repo.sync_to_vector_db(server, is_delete=is_delete)
    if not result or result.get("failed_tools"):
        raise RuntimeError(result.get("error") if result else "vector sync returned no result")


async def _reindex_agent(repo: A2AAgentRepository, agent: A2AAgent, *, is_delete: bool = False) -> None:
    """Sync one agent into the fresh generation; raise on failure so run_bounded records it."""
    result = await repo.sync_to_vector_db(agent, is_delete=is_delete)
    if not result or result.get("failed"):
        raise RuntimeError(result.get("error") if result else "vector sync returned no result")


async def _sweep(cursor, handler, ensure_lease: Callable[[], Awaitable[None]]) -> tuple[int, int, set[str]]:
    """Re-embed a whole collection in bounded batches (never materialize it all), returning
    ``(failed, total, swept_ids)``. ``swept_ids`` is every entity id streamed, used later for the
    delete diff. ``ensure_lease`` is awaited before each batch so a pod whose lease was taken over
    stops wasting embed calls; correctness (no commit, no duplicates) is enforced elsewhere."""
    failed = total = 0
    swept_ids: set[str] = set()
    batch: list = []
    async for doc in cursor:
        batch.append(doc)
        swept_ids.add(str(doc.id))
        if len(batch) >= _REINDEX_BATCH_SIZE:
            await ensure_lease()
            failed, total = await _run_batch(batch, handler, failed, total)
            batch = []
    if batch:
        await ensure_lease()
        failed, total = await _run_batch(batch, handler, failed, total)
    return failed, total, swept_ids


async def _run_batch(batch: list, handler, failed: int, total: int) -> tuple[int, int]:
    for result in await run_bounded(batch, handler, limit=_REINDEX_CONCURRENCY):
        total += 1
        if not result.ok:
            failed += 1
            logger.error("Reindex failed for %s", getattr(result.item, "id", result.item), exc_info=result.exc_info)
    return failed, total


class EmbeddingReindexExecutionService:
    """Run one already-claimed reindex job: sweep into a new generation, commit, grace, complete.

    ``run_claimed_job`` is resumable: it branches on the selection's current generation so a job
    reclaimed after a crash in any phase — and two jobs racing — converge consistently.
    """

    def __init__(
        self, *, db_client: DatabaseClient, settings: Settings, federation_job_service: FederationJobService
    ) -> None:
        self._db_client = db_client
        self._settings = settings
        self._federation_job_service = federation_job_service
        self._grace_seconds = settings.embedding_reindex_grace_period_seconds
        # None → derive from the MCP client timeouts (the default); an override lets ops/tests shorten it.
        override = settings.embedding_reindex_catch_up_min_delay_seconds
        self._catch_up_min_delay = timedelta(seconds=override) if override is not None else _CATCH_UP_MIN_DELAY

    async def _transition_or_lost(self, job: EmbeddingReindexJob, *, lease_owner: str, set_fields: dict) -> None:
        """Apply a lease-checked write, or raise if this pod no longer owns the job."""
        if not await transition_embedding_reindex_job(job_id=job.id, lease_owner=lease_owner, set_fields=set_fields):
            raise EmbeddingReindexLeaseLostError(f"Lost lease for embedding reindex job {job.id}")

    def _lease_checker(self, job: EmbeddingReindexJob, lease_owner: str) -> Callable[[], Awaitable[None]]:
        async def _ensure() -> None:
            owned = await EmbeddingReindexJob.get_pymongo_collection().find_one(
                {"_id": job.id, "status": EmbeddingReindexJobStatus.RUNNING.value, "leaseOwner": lease_owner}
            )
            if owned is None:
                raise EmbeddingReindexLeaseLostError(f"Lost lease for embedding reindex job {job.id}")

        return _ensure

    async def run_claimed_job(self, job: EmbeddingReindexJob, *, lease_owner: str) -> None:
        my_generation = str(job.id)
        selection = await get_model_gateway_selection(create_if_missing=True)
        current_generation = selection.embeddingCollectionGeneration if selection else None

        # Resume branch 1 — already committed (crash between the commit and the job's terminal save).
        if current_generation == my_generation:
            await self._grace_then_complete(job, lease_owner=lease_owner)
            return

        # Resume branch 2 — superseded: live generation is neither ours nor our captured previous.
        if current_generation != job.previousCollectionGeneration:
            await self._fail_job(job, "Superseded by a newer embedding reindex", lease_owner=lease_owner)
            return

        # Sweep branch.
        model_source = await ModelSource.get(job.targetEmbeddingModelSourceId)
        if model_source is None or model_source.deletedAt is not None:
            await self._fail_job(
                job,
                "Target embedding model source no longer exists; embedding model NOT switched",
                lease_owner=lease_owner,
            )
            return

        new_config = build_backend_config_from_model_source(
            model_source,
            self._settings.vector_config,
            encryption_key=self._settings.encryption_key,
            collection_generation=my_generation,
        )
        # Job-local client: own connection (blocking -> off the loop), ungated so its sweep writes.
        job_local_client = await asyncio.to_thread(create_database_client, new_config)
        try:
            mcp_repo = MCPServerRepository(job_local_client)
            a2a_repo = A2AAgentRepository(job_local_client)

            # Drop any leftover from a prior attempt of THIS job, then create both up front so an
            # empty corpus still yields queryable collections.
            _drop_collection(job_local_client, mcp_repo.collection)
            _drop_collection(job_local_client, a2a_repo.collection)
            await mcp_repo.ensure_collection()
            await a2a_repo.ensure_collection()

            # Stream both collections in bounded batches, checking the lease before each batch.
            ensure_lease = self._lease_checker(job, lease_owner)
            mcp_failed, mcp_total, swept_mcp_ids = await _sweep(
                ExtendedMCPServer.find_all(), lambda s: _reindex_server(mcp_repo, s), ensure_lease
            )
            a2a_failed, a2a_total, swept_a2a_ids = await _sweep(
                A2AAgent.find_all(), lambda a: _reindex_agent(a2a_repo, a), ensure_lease
            )
            failed = mcp_failed + a2a_failed
            total = mcp_total + a2a_total
            if failed:
                await self._fail_job(
                    job,
                    f"{failed}/{total} documents failed to re-embed; embedding model NOT switched",
                    lease_owner=lease_owner,
                )
                return

            # Catch up on writes that passed the gate before it closed and committed after the sweep
            # streamed past them, then commit a generation consistent with Mongo. _Aborted means the
            # catch-up already marked the job FAILED (federation drain timeout).
            try:
                catch_up_failed, catch_up_total = await self._catch_up(
                    job, mcp_repo, a2a_repo, ensure_lease, swept_mcp_ids, swept_a2a_ids, lease_owner=lease_owner
                )
            except _Aborted:
                return
            if catch_up_failed:
                await self._fail_job(
                    job,
                    f"{catch_up_failed}/{total + catch_up_total} documents failed to re-embed; "
                    "embedding model NOT switched",
                    lease_owner=lease_owner,
                )
                return

            switched = await self._commit_generation(job, my_generation=my_generation, lease_owner=lease_owner)
            if not switched:
                await self._fail_job(job, "Superseded by a newer embedding reindex", lease_owner=lease_owner)
                return
            logger.info("Embedding reindex job %s committed generation %s", job.id, my_generation)
        finally:
            await asyncio.to_thread(job_local_client.close)

        await self._grace_then_complete(job, lease_owner=lease_owner)

    async def _commit_generation(self, job: EmbeddingReindexJob, *, my_generation: str, lease_owner: str) -> bool:
        """Set ``switchedAt`` and flip the selection in ONE transaction, gated on the lease.

        Returns True on a committed switch, False when the compare-and-set lost the race (caller marks
        the job superseded). Raises ``EmbeddingReindexLeaseLostError`` if this pod no longer owns the
        job — the transaction rolls back, so a taken-over pod can never move the selection.
        """
        now = datetime.now(UTC)
        try:
            async with MongoDB.get_client().start_session() as session:
                async with await session.start_transaction():
                    if not await transition_embedding_reindex_job(
                        job_id=job.id, lease_owner=lease_owner, set_fields={"switchedAt": now}, session=session
                    ):
                        raise EmbeddingReindexLeaseLostError(f"Lost lease for embedding reindex job {job.id}")
                    committed = await commit_embedding_generation(
                        expected_generation=job.previousCollectionGeneration,
                        model_source_id=job.targetEmbeddingModelSourceId,
                        generation=my_generation,
                        updated_by=job.requestedBy,
                        session=session,
                    )
                    if committed is None:
                        raise _Superseded
        except _Superseded:
            return False
        job.switchedAt = now  # keep the in-memory copy so the grace window is measured correctly
        return True

    async def _catch_up(
        self,
        job: EmbeddingReindexJob,
        mcp_repo: MCPServerRepository,
        a2a_repo: A2AAgentRepository,
        ensure_lease: Callable[[], Awaitable[None]],
        swept_mcp_ids: set[str],
        swept_a2a_ids: set[str],
        *,
        lease_owner: str,
    ) -> tuple[int, int]:
        """Between the sweep and the commit, reconcile the new generation with Mongo.

        Waits out writers that passed the gate before it closed (so every such write has committed),
        re-syncs documents changed since a watermark, and deletes entities that no longer exist.
        Returns ``(failed, total)`` for the re-synced documents; raises ``_Aborted`` if it already
        failed the job (federation drain timeout), or ``EmbeddingReindexLeaseLostError`` on lease loss.
        """
        await self._await_pre_gate_writers(job, ensure_lease, lease_owner=lease_owner)

        since = _as_utc(job.startedAt) - _CATCH_UP_WATERMARK_MARGIN
        mcp_failed, mcp_total, mcp_ids = await _sweep(
            ExtendedMCPServer.find({"updatedAt": {"$gte": since}}),
            lambda s: _reindex_server(mcp_repo, s, is_delete=True),
            ensure_lease,
        )
        a2a_failed, a2a_total, a2a_ids = await _sweep(
            A2AAgent.find({"updatedAt": {"$gte": since}}),
            lambda a: _reindex_agent(a2a_repo, a, is_delete=True),
            ensure_lease,
        )
        swept_mcp_ids |= mcp_ids
        swept_a2a_ids |= a2a_ids

        failed = mcp_failed + a2a_failed
        if failed:
            return failed, mcp_total + a2a_total  # caller fails the job; skip deletes on a dirty gen

        await ensure_lease()
        await self._delete_removed(mcp_repo, a2a_repo, swept_mcp_ids, swept_a2a_ids)
        return 0, mcp_total + a2a_total

    async def _await_pre_gate_writers(
        self, job: EmbeddingReindexJob, ensure_lease: Callable[[], Awaitable[None]], *, lease_owner: str
    ) -> None:
        """Block until every write that passed the gate before it closed has committed.

        Two waits, polled together: a fixed minimum measured from ``startedAt`` covering the longest
        gated request path, and a drain of federation syncs that were already running (new ones are
        gated out). A federation sync stuck past ``_FEDERATION_DRAIN_TIMEOUT`` fails the job rather
        than wedging writes forever. ``ensure_lease`` runs on every tick so a lost lease stops the wait.
        """
        started = _as_utc(job.startedAt)
        min_delay_deadline = started + self._catch_up_min_delay
        drain_deadline = started + _FEDERATION_DRAIN_TIMEOUT
        while True:
            await ensure_lease()
            now = datetime.now(UTC)
            waiting_on_requests = now < min_delay_deadline
            federation_active = await self._federation_job_service.has_active_jobs()
            if not waiting_on_requests and not federation_active:
                return
            if federation_active and now >= drain_deadline:
                await self._fail_job(job, _FEDERATION_DRAIN_TIMEOUT_MSG, lease_owner=lease_owner)
                raise _Aborted
            await asyncio.sleep(_CATCH_UP_POLL_SECONDS)

    async def _delete_removed(
        self,
        mcp_repo: MCPServerRepository,
        a2a_repo: A2AAgentRepository,
        swept_mcp_ids: set[str],
        swept_a2a_ids: set[str],
    ) -> None:
        """Drop, from the new generation, entities that were swept but no longer exist in Mongo."""
        mongo_mcp_ids = {str(d["_id"]) async for d in ExtendedMCPServer.get_pymongo_collection().find({}, {"_id": 1})}
        mongo_a2a_ids = {str(d["_id"]) async for d in A2AAgent.get_pymongo_collection().find({}, {"_id": 1})}
        for gone in swept_mcp_ids - mongo_mcp_ids:
            await mcp_repo.delete_by_server_id(gone)
        for gone in swept_a2a_ids - mongo_a2a_ids:
            await a2a_repo.delete_by_agent_id(gone)

    async def finish_exhausted_job(self, job: EmbeddingReindexJob, *, lease_owner: str) -> None:
        """Finalize a job that has used up its retries: complete it if the switch already happened
        (every pod has swapped), otherwise fail it with the last error."""
        selection = await get_model_gateway_selection(create_if_missing=False)
        if selection is not None and selection.embeddingCollectionGeneration == str(job.id):
            await self._grace_then_complete(job, lease_owner=lease_owner)
            return
        await self._fail_job(
            job, f"Gave up after {_MAX_ATTEMPTS} attempts; last error: {job.lastError}", lease_owner=lease_owner
        )

    async def _grace_then_complete(self, job: EmbeddingReindexJob, *, lease_owner: str) -> None:
        """Keep the job RUNNING (writes 503'd) for the grace window, then complete — all under the lease.

        The previous generation is NOT dropped here — it stays readable for pods that have not swapped
        yet and is reclaimed later by GC. Grace only holds writes until every pod follows the selection.
        """
        if job.switchedAt is None:
            now = datetime.now(UTC)
            await self._transition_or_lost(job, lease_owner=lease_owner, set_fields={"switchedAt": now})
            job.switchedAt = now
        remaining = self._grace_seconds - (datetime.now(UTC) - _as_utc(job.switchedAt)).total_seconds()
        if remaining > 0:
            await asyncio.sleep(remaining)

        await self._transition_or_lost(
            job,
            lease_owner=lease_owner,
            set_fields={
                "status": EmbeddingReindexJobStatus.COMPLETED.value,
                "finishedAt": datetime.now(UTC),
                "leaseOwner": None,
                "leaseExpiresAt": None,
            },
        )
        logger.info("Embedding reindex job %s completed", job.id)

    async def _fail_job(self, job: EmbeddingReindexJob, error: str, *, lease_owner: str) -> None:
        """Mark the job FAILED and release its lease; nothing was committed, so live state is intact.

        Raises ``EmbeddingReindexLeaseLostError`` if the lease is gone (another owner will finalize it).
        """
        await self._transition_or_lost(
            job,
            lease_owner=lease_owner,
            set_fields={
                "status": EmbeddingReindexJobStatus.FAILED.value,
                "error": error,
                "finishedAt": datetime.now(UTC),
                "leaseOwner": None,
                "leaseExpiresAt": None,
            },
        )
        logger.error("Embedding reindex job %s failed: %s", job.id, error)
