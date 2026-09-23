"""Execution of a claimed embedding-reindex job: re-embed every document, then swap the live adapter."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from registry.core.config import Settings
from registry.core.vector_backend import build_backend_config_from_model_source
from registry.utils.concurrency import run_bounded
from registry_pkgs.database.model_gateway_selection_repository import set_model_gateway_selection
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus
from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.vector.adapters.adapter import VectorStoreAdapter
from registry_pkgs.vector.client import DatabaseClient, create_database_client
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

logger = logging.getLogger(__name__)

# Bounded so one slow/rate-limited embedding call can't serialize the whole sweep and a handful of
# per-document failures don't crash the run — they're collected and fail the job as a whole.
_REINDEX_CONCURRENCY = 5


def _close_adapter(adapter: VectorStoreAdapter | None) -> None:
    """Close a swapped-out adapter using the same pattern as ``DatabaseClient.close``."""
    if adapter is None:
        return
    try:
        if hasattr(adapter, "close"):
            adapter.close()
        elif hasattr(adapter, "__exit__"):
            adapter.__exit__(None, None, None)
    except Exception:  # noqa: BLE001 - a failed close of the retired adapter must not fail the job
        logger.exception("Failed to close retired vector adapter after reindex swap")


async def _reindex_server(repo: MCPServerRepository, server: ExtendedMCPServer) -> None:
    """Re-embed one server; raise on failure so ``run_bounded`` records it (the repo swallows its own)."""
    result = await repo.sync_to_vector_db(server, is_delete=True)
    if not result or result.get("failed_tools"):
        raise RuntimeError(result.get("error") if result else "vector sync returned no result")


async def _reindex_agent(repo: A2AAgentRepository, agent: A2AAgent) -> None:
    """Re-embed one agent; raise on failure so ``run_bounded`` records it (the repo swallows its own)."""
    result = await repo.sync_to_vector_db(agent, is_delete=True)
    if not result or result.get("failed"):
        raise RuntimeError(result.get("error") if result else "vector sync returned no result")


class EmbeddingReindexExecutionService:
    """Run one already-claimed reindex job: re-embed the whole corpus, then swap the live adapter.

    Business logic only — claiming, leasing, and heartbeats belong to the runner. The sweep rewrites
    the shared Weaviate collection in place, so once it has run the adapter is ALWAYS swapped to the
    new model: leaving it on the old model would embed queries in the old space and compare them
    against a collection now in the new space (mixed/wrong results). A document that fails to re-embed
    is simply absent from search until its next per-document sync re-adds it — the same graceful,
    self-healing degradation as an ordinary CRUD sync failure — rather than corrupting results.
    """

    def __init__(self, *, db_client: DatabaseClient, settings: Settings) -> None:
        self._db_client = db_client
        self._settings = settings

    async def run_claimed_job(self, job: EmbeddingReindexJob) -> None:
        """Re-embed every server and agent against the job's target model, then swap the live adapter."""
        model_source = await ModelSource.get(job.targetEmbeddingModelSourceId)
        if model_source is None:
            await self._fail_job(job, "Target embedding model source no longer exists; embedding model NOT swapped")
            return

        new_config = build_backend_config_from_model_source(
            model_source,
            self._settings.vector_config,
            encryption_key=self._settings.encryption_key,
        )
        # A separate client that does NOT go through container.db_client, so its own repository
        # calls are not blocked by the AS-1867 gate (which only guards the shared client). It owns a
        # freshly opened Weaviate/HTTP adapter, so it must be closed on every path that does NOT hand
        # that adapter to container.db_client via the swap below — otherwise a failed/retried job
        # leaks connections.
        job_local_client = create_database_client(new_config)
        promoted = False
        try:
            job_local_mcp_repo = MCPServerRepository(job_local_client)
            job_local_a2a_repo = A2AAgentRepository(job_local_client)

            # Safe to enumerate the full corpus with plain find_all(): AS-1867 blocks every write for
            # the job's whole duration, so there is no moving target mid-sweep.
            servers = await ExtendedMCPServer.find_all().to_list()
            agents = await A2AAgent.find_all().to_list()

            server_results = await run_bounded(
                servers, lambda s: _reindex_server(job_local_mcp_repo, s), limit=_REINDEX_CONCURRENCY
            )
            agent_results = await run_bounded(
                agents, lambda a: _reindex_agent(job_local_a2a_repo, a), limit=_REINDEX_CONCURRENCY
            )

            failures = [r for r in (*server_results, *agent_results) if not r.ok]

            # The sweep already rewrote the shared collection in place, so the adapter MUST follow it
            # to the new model regardless of per-document failures — see the class docstring. Reuse
            # the job-local adapter directly (the sweep proved it works).
            old_adapter = self._db_client.swap_adapter(job_local_client.adapter, new_config)
            # Ownership of job_local_client's adapter has now transferred to container.db_client, so
            # job_local_client must NOT be closed on the way out.
            promoted = True
            try:
                # Commit the gateway selection right after the in-memory swap (deferred from trigger
                # time) so a failed/crashed reindex never leaves the selection naming a model the index
                # was not rebuilt with, and so the swap→commit gap stays as small as possible.
                await set_model_gateway_selection("embeddingModelSourceId", model_source.id, updated_by=job.triggeredBy)
            finally:
                # Always release the retired adapter, even if the selection commit fails.
                await asyncio.to_thread(_close_adapter, old_adapter)

            job.status = EmbeddingReindexJobStatus.COMPLETED
            job.finishedAt = datetime.now(UTC)
            job.leaseOwner = None
            job.leaseExpiresAt = None
            if failures:
                failed_ids = [str(getattr(f.item, "id", f.item)) for f in failures]
                for failure in failures:
                    logger.error(
                        "Reindex failed for %s", getattr(failure.item, "id", failure.item), exc_info=failure.exc_info
                    )
                # Recorded so the partial result is observable; these documents rejoin search on next sync.
                job.error = (
                    f"{len(failures)}/{len(servers) + len(agents)} documents failed to re-embed and are "
                    f"temporarily missing from search until their next sync: {failed_ids}"
                )
            await job.save()
            logger.info(
                "Embedding reindex job %s completed (%d failed); live adapter swapped to model %s",
                job.id,
                len(failures),
                model_source.id,
            )
        finally:
            if not promoted:
                # Config/enumeration/sync/cancellation failed before the swap, so the job-local
                # adapter was never handed off — close it to avoid leaking Weaviate/HTTP connections.
                await asyncio.to_thread(job_local_client.close)

    @staticmethod
    async def _fail_job(job: EmbeddingReindexJob, error: str) -> None:
        """Mark the job FAILED and release its lease so the gate clears; the live adapter is untouched."""
        job.status = EmbeddingReindexJobStatus.FAILED
        job.error = error
        job.finishedAt = datetime.now(UTC)
        job.leaseOwner = None
        job.leaseExpiresAt = None
        await job.save()
        logger.error("Embedding reindex job %s failed: %s", job.id, error)
