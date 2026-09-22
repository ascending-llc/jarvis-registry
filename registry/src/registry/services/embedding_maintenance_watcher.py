from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.database.embedding_reindex_job_repository import get_active_embedding_reindex_job

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0


class EmbeddingMaintenanceWatcher:
    """Cache whether an embedding-model reindex is currently active."""

    def __init__(self) -> None:
        self._active = False
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start exactly one polling task for this app-scoped watcher."""
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="embedding-maintenance-watcher")

    async def shutdown(self) -> None:
        """Stop polling; safe when never started."""
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    def is_active(self) -> bool:
        """Return the cached active flag (defaults to False until a poll sets it)."""
        return self._active

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._active = (await get_active_embedding_reindex_job()) is not None
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Embedding maintenance watcher poll failed")
            await self._wait_for_next_poll()

    async def _wait_for_next_poll(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=_POLL_INTERVAL_SECONDS)
        except TimeoutError:
            return


def raise_if_reindex_active(watcher: EmbeddingMaintenanceWatcher | None) -> None:
    """Raise ``EmbeddingReindexInProgressException`` when a reindex is active.

    A ``None`` watcher (unwired, e.g. in a unit test) is treated as not active.
    """
    if watcher is not None and watcher.is_active():
        raise EmbeddingReindexInProgressException("An embedding-model reindex is in progress")
