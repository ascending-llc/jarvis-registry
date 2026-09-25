from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from registry.core.config import Settings
from registry.core.vector_backend import resolve_vector_backend_config
from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.database.embedding_reindex_job_repository import get_active_embedding_reindex_job
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.vector.adapters.factory import VectorStoreFactory
from registry_pkgs.vector.client import DatabaseClient, collection_name_for

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0
# Startup GC already runs once at boot; this reclaims generations that pile up between restarts when
# the model is switched repeatedly on a long-lived cluster. It no-ops while a reindex is active.
_GC_INTERVAL_SECONDS = 1800.0


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
        self._stale = False
        self._retired_adapter = None
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
        # The deferred close normally happens on the next poll; on shutdown there is no next poll.
        if self._retired_adapter is not None:
            await asyncio.to_thread(_close_adapter, self._retired_adapter)
            self._retired_adapter = None

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
            await self._wait_for_next_poll()

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
        # Deferred close: retire last poll's old adapter now, giving in-flight reads one interval.
        if self._retired_adapter is not None:
            await asyncio.to_thread(_close_adapter, self._retired_adapter)
            self._retired_adapter = None

        self._job_active = (await get_active_embedding_reindex_job()) is not None

        if self._db_client is None or self._settings is None:
            return  # unwired (e.g. a unit test): job-active tracking only, no swap.

        # Reuse the startup resolver so the target config (incl. legacy fallback and fail-hard) is
        # derived identically everywhere. A failure keeps this pod stale and retries next poll.
        try:
            target_config = await resolve_vector_backend_config(self._settings)
        except Exception:
            self._stale = True
            logger.exception("Watcher could not resolve the target backend config; retrying next poll")
            return

        target_generation = target_config.collection_generation
        if target_generation == self._db_client.collection_generation:
            self._stale = False
            return

        # This pod is behind the active generation: keep writes blocked until it swaps.
        self._stale = True
        try:
            new_adapter = await asyncio.to_thread(VectorStoreFactory.create_adapter, target_config)
        except Exception:
            logger.exception("Watcher failed to build adapter for generation %s; retrying next poll", target_generation)
            return
        old_adapter = self._db_client.swap_adapter(new_adapter, target_config)
        self._retired_adapter = old_adapter  # closed on the next poll
        self._stale = False
        logger.info("Watcher swapped this pod to embedding generation %s", target_generation)

    async def _wait_for_next_poll(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=_POLL_INTERVAL_SECONDS)
        except TimeoutError:
            return


async def gc_stale_embedding_generations(db_client: DatabaseClient) -> None:
    """Drop ``<base>_<generation>`` collections other than the active generation. Best-effort.

    Reclaims generations left by failed/superseded reindexes and by past switches. Base/legacy
    collections are never dropped. Skips entirely while a reindex is active — another pod may be
    sweeping into a new generation or still lagging on the previous one, and dropping either would
    corrupt it; the orphans are harmless until the next idle GC.
    """
    try:
        if await get_active_embedding_reindex_job() is not None:
            return
        selection = await get_model_gateway_selection(create_if_missing=False)
        active_generation = selection.embeddingCollectionGeneration if selection else None
        adapter = db_client.adapter  # ungated read adapter; drop_collection lives on the same object
        if not hasattr(adapter, "list_collections") or not hasattr(adapter, "drop_collection"):
            return
        bases = (ExtendedMCPServer.COLLECTION_NAME, A2AAgent.COLLECTION_NAME)
        active_names = {collection_name_for(base, active_generation) for base in bases}
        for name in adapter.list_collections() or []:
            if name in active_names:
                continue
            if any(name.startswith(f"{base}_") for base in bases):
                try:
                    adapter.drop_collection(name)
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
