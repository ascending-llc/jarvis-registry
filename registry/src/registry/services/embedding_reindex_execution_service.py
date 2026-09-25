"""Execution of a claimed embedding-reindex job using collection generations.

Re-embeds the corpus into a new collection generation (``<Base>_<jobId>``), then commits the
``(model, generation)`` pair with one compare-and-set. Never swaps the shared adapter — every pod
follows the selection via its own watcher. On failure nothing is committed; the abandoned generation
is reclaimed later by GC.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from registry.core.config import Settings
from registry.core.vector_backend import build_backend_config_from_model_source
from registry.utils.concurrency import run_bounded
from registry_pkgs.database.model_gateway_selection_repository import (
    commit_embedding_generation,
    get_model_gateway_selection,
)
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus
from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.vector.client import DatabaseClient, create_database_client
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

logger = logging.getLogger(__name__)

_REINDEX_CONCURRENCY = 5
# Keep writes blocked (job stays RUNNING) after the commit long enough for every pod's watcher to
# swap to the new generation. Configurable via settings; falls back to 60s.
_DEFAULT_GRACE_PERIOD_SECONDS = 60.0


def _drop_collection(client: DatabaseClient, name: str) -> None:
    """Drop a Weaviate collection by name via the (ungated) job-local client; tolerate 'not found'."""
    adapter = client.write_adapter  # job-local client has no reindex gate wired
    if not hasattr(adapter, "drop_collection"):
        return
    try:
        adapter.drop_collection(name)
    except Exception:  # noqa: BLE001 - a resumed job may have already dropped it
        logger.warning("Could not drop collection '%s' (may not exist)", name)


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

    async def run_claimed_job(self, job: EmbeddingReindexJob) -> None:
        my_generation = str(job.id)
        selection = await get_model_gateway_selection(create_if_missing=True)
        current_generation = selection.embeddingCollectionGeneration if selection else None

        # Resume branch 1 — already committed (crash between the commit and the job's terminal save).
        if current_generation == my_generation:
            await self._grace_then_complete(job)
            return

        # Resume branch 2 — superseded: live generation is neither ours nor our captured previous.
        if current_generation != job.previousCollectionGeneration:
            await self._fail_job(job, "Superseded by a newer embedding reindex")
            return

        # Sweep branch.
        model_source = await ModelSource.get(job.targetEmbeddingModelSourceId)
        if model_source is None or model_source.deletedAt is not None:
            await self._fail_job(job, "Target embedding model source no longer exists; embedding model NOT switched")
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

            # ponytail: whole corpus in memory; batch with a cursor if the registry ever holds
            # enough servers/agents for this to matter.
            servers = await ExtendedMCPServer.find_all().to_list()
            agents = await A2AAgent.find_all().to_list()
            server_results = await run_bounded(
                servers, lambda s: _reindex_server(mcp_repo, s), limit=_REINDEX_CONCURRENCY
            )
            agent_results = await run_bounded(agents, lambda a: _reindex_agent(a2a_repo, a), limit=_REINDEX_CONCURRENCY)
            failures = [r for r in (*server_results, *agent_results) if not r.ok]
            if failures:
                for failure in failures:
                    logger.error(
                        "Reindex failed for %s", getattr(failure.item, "id", failure.item), exc_info=failure.exc_info
                    )
                await self._fail_job(
                    job,
                    f"{len(failures)}/{len(servers) + len(agents)} documents failed to re-embed; "
                    "embedding model NOT switched",
                )
                return

            committed = await commit_embedding_generation(
                expected_generation=job.previousCollectionGeneration,
                model_source_id=job.targetEmbeddingModelSourceId,
                generation=my_generation,
                updated_by=job.requestedBy,
            )
            if committed is None:
                # Another reindex committed while we swept; our generation is now abandoned.
                await self._fail_job(job, "Superseded by a newer embedding reindex")
                return

            job.switchedAt = datetime.now(UTC)
            await job.save()
            logger.info("Embedding reindex job %s committed generation %s", job.id, my_generation)
        finally:
            await asyncio.to_thread(job_local_client.close)

        await self._grace_then_complete(job)

    async def _grace_then_complete(self, job: EmbeddingReindexJob) -> None:
        """Keep the job RUNNING (writes 503'd) for the grace window, then complete.

        The previous generation is NOT dropped here — it stays readable for pods that have not swapped
        yet and is reclaimed later by GC. Grace only holds writes until every pod follows the selection.
        """
        if job.switchedAt is None:
            job.switchedAt = datetime.now(UTC)
            await job.save()
        # Mongo returns datetimes tz-naive (stored as UTC); make it aware before subtracting.
        switched_at = job.switchedAt if job.switchedAt.tzinfo else job.switchedAt.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - switched_at).total_seconds()
        remaining = self._grace_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

        job.status = EmbeddingReindexJobStatus.COMPLETED
        job.finishedAt = datetime.now(UTC)
        job.leaseOwner = None
        job.leaseExpiresAt = None
        await job.save()
        logger.info("Embedding reindex job %s completed", job.id)

    @staticmethod
    async def _fail_job(job: EmbeddingReindexJob, error: str) -> None:
        """Mark the job FAILED and release its lease; nothing was committed, so the live state is intact."""
        job.status = EmbeddingReindexJobStatus.FAILED
        job.error = error
        job.finishedAt = datetime.now(UTC)
        job.leaseOwner = None
        job.leaseExpiresAt = None
        await job.save()
        logger.error("Embedding reindex job %s failed: %s", job.id, error)
