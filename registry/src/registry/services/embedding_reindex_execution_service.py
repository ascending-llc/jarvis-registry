"""Execution of a claimed embedding-reindex job: re-embed every document, then swap the live adapter."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from registry.core.config import Settings
from registry.core.vector_backend import build_backend_config_from_model_source
from registry.utils.concurrency import run_bounded
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
    """Run one already-claimed reindex job: re-embed the whole corpus, then atomically swap adapters.

    Business logic only — claiming, leasing, and heartbeats belong to the runner. On any per-item
    failure the swap is deliberately skipped and the live adapter is left on the old, internally
    consistent model, because a partial sweep is exactly the mixed-vector-space bug this fixes.
    """

    def __init__(self, *, db_client: DatabaseClient, settings: Settings) -> None:
        self._db_client = db_client
        self._settings = settings

    async def run_claimed_job(self, job: EmbeddingReindexJob) -> None:
        """Re-embed every server and agent against the job's target model, then swap or fail."""
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
        # calls are not blocked by the AS-1867 gate (which only guards the shared client).
        job_local_client = create_database_client(new_config)
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
        if failures:
            for failure in failures:
                logger.error(
                    "Reindex failed for %s", getattr(failure.item, "id", failure.item), exc_info=failure.exc_info
                )
            await self._fail_job(
                job,
                f"{len(failures)}/{len(servers) + len(agents)} documents failed to re-embed; "
                "embedding model NOT swapped",
            )
            return

        # Reuse the job-local client's adapter directly — the sweep already proved it works, so
        # rebuilding a third time would only cost a redundant reconnect. Do NOT close job_local_client
        # afterward: container.db_client now references this same adapter.
        old_adapter = self._db_client.swap_adapter(job_local_client.adapter, new_config)
        # Closing a vector adapter tears down gRPC/HTTP connections and can block; keep it off the loop.
        await asyncio.to_thread(_close_adapter, old_adapter)

        job.status = EmbeddingReindexJobStatus.COMPLETED
        job.finishedAt = datetime.now(UTC)
        job.leaseOwner = None
        job.leaseExpiresAt = None
        await job.save()
        logger.info("Embedding reindex job %s completed; live adapter swapped to model %s", job.id, model_source.id)

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
