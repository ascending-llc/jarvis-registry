from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry.core.config import Settings
from registry.core.vector_backend import resolve_vector_backend_config
from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.database.embedding_reindex_job_repository import get_active_embedding_reindex_job
from registry_pkgs.database.leased_job import interruptible_sleep
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus
from registry_pkgs.vector.adapters.factory import VectorStoreFactory
from registry_pkgs.vector.client import DatabaseClient

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
# Startup GC already runs once at boot; this reclaims generations that pile up between restarts when
# the model is switched repeatedly on a long-lived cluster. It no-ops while a reindex is active.
_GC_INTERVAL_SECONDS = 1800.0
# A swapped-out adapter is closed only after this long, so a slow search still running on it in a
# to_thread worker is never cut off. It is comfortably above Weaviate's own query timeout, so no
# in-flight read can outlive it; the cost is holding a few idle connections briefly after a rare swap.
_RETIRED_ADAPTER_GRACE_SECONDS = 300.0
# A just-superseded generation is kept this long after its switch COMPLETED, so a pod that has not
# swapped yet can still read it during the grace period. Far longer than the real swap window.
_PREVIOUS_GENERATION_RETENTION = timedelta(minutes=10)


def _close_adapter(adapter) -> None:
    if adapter is None:
        return
    try:
        if hasattr(adapter, "close"):
            adapter.close()
        elif hasattr(adapter, "__exit__"):
            adapter.__exit__(None, None, None)
    except Exception:  # noqa: BLE001 - a failed close of a retired adapter must not fail the watcher
        logger.exception("Failed to close retired vector adapter")


class EmbeddingMaintenanceWatcher:
    """Follow the active embedding generation and swap THIS pod's adapter to match.

    Every pod runs one. Each poll compares the selection's generation with the one this pod is built
    for; on a difference it builds and swaps in a new adapter, so all pods converge on the committed
    ``(model, generation)`` pair. The retired adapter is closed on the next poll so in-flight reads
    can finish.

    ``is_active()`` gates writes (via ``DatabaseClient.write_adapter``): true while a reindex is active
    OR this pod has not yet swapped, so a lagging pod 503s writes instead of writing a doomed generation.
    """

    def __init__(self, *, db_client: DatabaseClient | None = None, settings: Settings | None = None) -> None:
        self._db_client = db_client
        self._settings = settings
        self._job_active = False
        # Fail safe: a wired pod blocks writes until its first poll confirms it is on the active
        # generation, so a pod that starts mid-reindex cannot accept writes in that startup window.
        # An unwired watcher (no db_client, e.g. a unit test) only tracks job-active, never stale.
        self._stale = db_client is not None
        # Adapters retired by a swap, each with the time it was retired; closed once past the grace
        # window (a list, not one slot, so rapid back-to-back swaps never leak the middle adapter).
        self._retired: list[tuple[object, float]] = []
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_gc = 0.0

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._last_gc = time.monotonic()  # startup GC already ran; defer the first periodic sweep
        self._task = asyncio.create_task(self._run(), name="embedding-maintenance-watcher")

    async def shutdown(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        # Runtime close waits out the grace window; on shutdown there is no next poll, so close the
        # rest now. In-flight reads are ending with the process anyway.
        while self._retired:
            adapter, _ = self._retired.pop()
            await asyncio.to_thread(_close_adapter, adapter)

    def is_active(self) -> bool:
        """True while a reindex job is active OR this pod has not yet swapped to the active generation."""
        return self._job_active or self._stale

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll()
                await self._maybe_gc()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Embedding maintenance watcher poll failed")
            await interruptible_sleep(self._stop_event, _POLL_INTERVAL_SECONDS)

    async def _close_expired_adapters(self) -> None:
        """Close adapters retired more than the grace window ago; keep the rest for in-flight reads."""
        if not self._retired:
            return
        now = time.monotonic()
        keep: list[tuple[object, float]] = []
        for adapter, retired_at in self._retired:
            if now - retired_at >= _RETIRED_ADAPTER_GRACE_SECONDS:
                await asyncio.to_thread(_close_adapter, adapter)
            else:
                keep.append((adapter, retired_at))
        self._retired = keep

    async def _maybe_gc(self) -> None:
        """Periodically reclaim superseded generations so they do not pile up between restarts."""
        if self._db_client is None:
            return
        now = time.monotonic()
        if now - self._last_gc < _GC_INTERVAL_SECONDS:
            return
        self._last_gc = now
        await gc_stale_embedding_generations(self._db_client)

    async def _poll(self) -> None:
        await self._close_expired_adapters()

        self._job_active = (await get_active_embedding_reindex_job()) is not None

        if self._db_client is None or self._settings is None:
            return  # unwired (e.g. a unit test): job-active tracking only, no swap.

        # Cheap steady-state path: just read the selection and compare generations. Only when this pod
        # is behind do we run the resolver (which fetches the ModelSource and decrypts its credentials).
        selection = await get_model_gateway_selection(create_if_missing=False)
        target_generation = selection.embeddingCollectionGeneration if selection else None
        if target_generation == self._db_client.collection_generation:
            self._stale = False
            return

        # This pod is behind the active generation: keep writes blocked until it swaps. Reuse the startup
        # resolver so the target config (incl. legacy fallback and fail-hard) is derived identically
        # everywhere. A failure keeps this pod stale and retries next poll.
        self._stale = True
        try:
            target_config = await resolve_vector_backend_config(self._settings)
        except Exception:
            logger.exception("Watcher could not resolve the target backend config; retrying next poll")
            return
        try:
            new_adapter = await asyncio.to_thread(VectorStoreFactory.create_adapter, target_config)
        except Exception:
            logger.exception("Watcher failed to build adapter for generation %s; retrying next poll", target_generation)
            return
        old_adapter = self._db_client.swap_adapter(new_adapter, target_config)
        if old_adapter is not None:
            self._retired.append((old_adapter, time.monotonic()))  # closed after the grace window
        self._stale = False
        logger.info("Watcher swapped this pod to embedding generation %s", target_generation)


def _generation_suffix(name: str, bases: tuple[str, ...]) -> str | None:
    """Return ``<g>`` for a ``<base>_<g>`` collection, or None for a base name / a name that is not
    one of ours (so the base collections are never touched)."""
    for base in bases:
        prefix = f"{base}_"
        if name.startswith(prefix):
            return name[len(prefix) :]
    return None


async def gc_stale_embedding_generations(db_client: DatabaseClient) -> None:
    """Drop per-generation collections that are provably safe to remove. Best-effort; never blocks.

    A ``<base>_<g>`` collection is dropped only when ALL hold:
      (a) ``g`` is a valid job id whose job is COMPLETED/FAILED — a finished job never writes again;
      (b) ``g`` is not the active generation;
      (c) ``g`` is not the previous generation of any RUNNING job (read with NO lease filter, so an
          expired-lease job still protects it);
      (d) ``g`` is not the previous generation of a job that COMPLETED within the retention window
          (a lagging pod may still be reading it during that job's grace).
    The base names and any suffix without a job document are never dropped. Only a pod already on the
    active generation runs GC (a stale pod still reads the previous one and must not drop it).
    """
    try:
        selection = await get_model_gateway_selection(create_if_missing=False)
        active = selection.embeddingCollectionGeneration if selection else None
        if db_client.collection_generation != active:
            return
        adapter = db_client.adapter  # ungated read adapter; drop_collection lives on the same object
        if not hasattr(adapter, "list_collections") or not hasattr(adapter, "drop_collection"):
            return

        jobs = EmbeddingReindexJob.get_pymongo_collection()
        running = jobs.find({"status": EmbeddingReindexJobStatus.RUNNING.value})
        protected = {doc.get("previousCollectionGeneration") async for doc in running}
        protected.discard(None)

        bases = (ExtendedMCPServer.COLLECTION_NAME, A2AAgent.COLLECTION_NAME)
        names = await asyncio.to_thread(adapter.list_collections) or []
        retention_cutoff = datetime.now(UTC) - _PREVIOUS_GENERATION_RETENTION
        for name in names:
            generation = _generation_suffix(name, bases)
            if generation is None or generation == active or generation in protected:
                continue  # base name / not ours (b) / RUNNING-protected (c)
            try:
                job_id = PydanticObjectId(generation)
            except (InvalidId, TypeError, ValueError):
                continue  # suffix isn't a job id -> not ours
            job = await EmbeddingReindexJob.get(job_id)
            if job is None or job.status not in (EmbeddingReindexJobStatus.COMPLETED, EmbeddingReindexJobStatus.FAILED):
                continue  # (a): only a finished job's generation may be dropped
            recently_superseded = await jobs.find_one(
                {
                    "status": EmbeddingReindexJobStatus.COMPLETED.value,
                    "previousCollectionGeneration": generation,
                    "finishedAt": {"$gt": retention_cutoff},
                }
            )
            if recently_superseded is not None:
                continue  # (d): still within a recent switch's retention window
            try:
                await asyncio.to_thread(adapter.drop_collection, name)
                logger.info("GC dropped stale embedding generation collection '%s'", name)
            except Exception:  # noqa: BLE001
                logger.warning("GC could not drop stale collection '%s'", name)
    except Exception:  # noqa: BLE001 - GC must never block startup
        logger.exception("Embedding generation GC failed (continuing startup)")


def raise_if_reindex_active(watcher: EmbeddingMaintenanceWatcher | None) -> None:
    """Raise ``EmbeddingReindexInProgressException`` when writes must be blocked.

    A ``None`` watcher (unwired, e.g. in a unit test) is treated as not active.
    """
    if watcher is not None and watcher.is_active():
        raise EmbeddingReindexInProgressException("An embedding-model reindex is in progress")
