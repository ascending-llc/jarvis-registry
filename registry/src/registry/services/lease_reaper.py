"""Per-pod loop that periodically runs the reaper tasks finalizing abandoned leased work.

When a pod dies mid-execution it stops renewing its leases, leaving documents behind that no live
process will ever finish — for example a ``WorkflowRun`` stuck ``RUNNING``. Each reaper task finds the
documents of one kind whose lease has expired and drives them to a terminal state. ``LeaseReaper``
only schedules those tasks: it runs each of them once per ``interval``.

Every pod runs its own ``LeaseReaper``; no leader election is required, because each task finalizes a
document with a single atomic ``find_one_and_update`` — two pods racing on the same document see only
one win. Tasks are registered as ``(name, reap)`` pairs where ``reap`` returns how many documents it
finalized — for example the ``WorkflowRun`` reaper, with more kinds added to the same list over time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import timedelta

from registry_pkgs.database.leased_job import interruptible_sleep

logger = logging.getLogger(__name__)


class LeaseReaper:
    """Runs each registered reap task once per ``interval`` on a single background loop per pod.

    A failure in one task is logged and isolated so it cannot stop the other tasks or the loop.
    """

    def __init__(
        self,
        tasks: list[tuple[str, Callable[[], Awaitable[int]]]],
        *,
        interval: timedelta = timedelta(seconds=30),
    ) -> None:
        """
        Args:
            tasks: ``(name, reap)`` pairs. ``reap`` is awaited once per tick and returns the number of
                documents it finalized; ``name`` is used only for logging.
            interval: Delay between the end of one full pass over ``tasks`` and the start of the next.
        """
        self._tasks = tasks
        self._interval_seconds = interval.total_seconds()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the reaper loop. Idempotent: a call while already running is a no-op."""
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="lease-reaper")

    async def shutdown(self) -> None:
        """Stop the loop and wait for the in-flight tick to unwind. Safe when never started."""
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _loop(self) -> None:
        """Run every task each tick until stopped, sleeping ``interval`` between passes.

        A non-zero finalize count is logged at ``warning`` — it means a pod died and left work behind.
        A task's own exception is logged and swallowed so one failing task never stops the others or
        the loop; only ``CancelledError`` (raised by ``shutdown``) is allowed to propagate.
        """
        while not self._stop_event.is_set():
            for name, reap in self._tasks:
                try:
                    count = await reap()
                    if count:
                        logger.warning("lease-reaper[%s]: finalized %d document(s)", name, count)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("lease-reaper[%s]: tick failed", name)
            await interruptible_sleep(self._stop_event, self._interval_seconds)
