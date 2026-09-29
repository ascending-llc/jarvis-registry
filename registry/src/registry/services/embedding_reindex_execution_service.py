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
from datetime import UTC, datetime

from registry.core.config import Settings
from registry.core.vector_backend import build_backend_config_from_model_source
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
# Keep writes blocked (job stays RUNNING) after the commit long enough for every pod's watcher to
# swap to the new generation. Configurable via settings; falls back to 60s.
_DEFAULT_GRACE_PERIOD_SECONDS = 60.0


class EmbeddingReindexLeaseLostError(RuntimeError):
    """The job's RUNNING lease is no longer held by this pod; it must stop all writes for the job."""


class _Superseded(Exception):
    """Internal: a compare-and-set miss inside the commit transaction, used to abort and roll back."""


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


async def _reindex_server(repo: MCPServerRepository, server: ExtendedMCPServer) -> None:
    """Insert one server into the fresh generation; raise on failure so run_bounded records it."""
    result = await repo.sync_to_vector_db(server, is_delete=False)
    if not result or result.get("failed_tools"):
        raise RuntimeError(result.get("error") if result else "vector sync returned no result")


async def _reindex_agent(repo: A2AAgentRepository, agent: A2AAgent) -> None:
    """Insert one agent into the fresh generation; raise on failure so run_bounded records it."""
    result = await repo.sync_to_vector_db(agent, is_delete=False)
    if not result or result.get("failed"):
        raise RuntimeError(result.get("error") if result else "vector sync returned no result")


async def _sweep(cursor, handler, ensure_lease: Callable[[], Awaitable[None]]) -> tuple[int, int]:
    """Re-embed a whole collection in bounded batches (never materialize it all), returning
    ``(failed, total)``. ``ensure_lease`` is awaited before each batch so a pod whose lease was taken
    over stops wasting embed calls; correctness (no commit, no duplicates) is enforced elsewhere."""
    failed = total = 0
    batch: list = []
    async for doc in cursor:
        batch.append(doc)
        if len(batch) >= _REINDEX_BATCH_SIZE:
            await ensure_lease()
            failed, total = await _run_batch(batch, handler, failed, total)
            batch = []
    if batch:
        await ensure_lease()
        failed, total = await _run_batch(batch, handler, failed, total)
    return failed, total


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

    def __init__(self, *, db_client: DatabaseClient, settings: Settings) -> None:
        self._db_client = db_client
        self._settings = settings
        self._grace_seconds = float(
            getattr(settings, "embedding_reindex_grace_period_seconds", _DEFAULT_GRACE_PERIOD_SECONDS)
        )

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
            mcp_failed, mcp_total = await _sweep(
                ExtendedMCPServer.find_all(), lambda s: _reindex_server(mcp_repo, s), ensure_lease
            )
            a2a_failed, a2a_total = await _sweep(
                A2AAgent.find_all(), lambda a: _reindex_agent(a2a_repo, a), ensure_lease
            )
            failed = mcp_failed + a2a_failed
            if failed:
                await self._fail_job(
                    job,
                    f"{failed}/{mcp_total + a2a_total} documents failed to re-embed; embedding model NOT switched",
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
        # Mongo returns datetimes tz-naive (stored as UTC); make it aware before subtracting.
        switched_at = job.switchedAt if job.switchedAt.tzinfo else job.switchedAt.replace(tzinfo=UTC)
        remaining = self._grace_seconds - (datetime.now(UTC) - switched_at).total_seconds()
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
