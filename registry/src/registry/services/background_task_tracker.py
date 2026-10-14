"""Process-wide registry of fire-and-forget asyncio tasks with a bounded, cooperative shutdown.

Background work that outlives the request that started it — a launched workflow run, a federation
sync, a vector reindex — was previously started with a bare ``asyncio.create_task``. Such a task has
two problems: Python silently discards any exception it raises, and nothing cancels it when the pod
shuts down. ``BackgroundTaskTracker`` addresses both. It keeps a reference to every task it starts,
logs any exception that escapes one, refuses new work once shutdown has begun, and on shutdown
cancels all tracked tasks and waits for them for a fixed budget.

A single instance is built in the container and shared by every producer of background tasks (the
workflow run launcher, federation sync, vector sync), so one ``shutdown()`` call drains them all.
The tracker owns task lifecycle only and knows nothing about
leases: a leased workflow task performs its own terminal write when cancelled (via its
``on_cancelled`` hook), so the tracker merely cancels, waits, and reports how many tasks were still
running when the budget elapsed. The lease reaper finalizes any stragglers within one lease duration.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from registry_pkgs.workflows.types import WorkflowConfigError

logger = logging.getLogger(__name__)

# How long ``shutdown`` waits for cancelled tasks to finalize before reporting them as still pending.
FINALIZE_BUDGET = timedelta(seconds=10)


class TrackerShutdownError(RuntimeError):
    """Raised by ``BackgroundTaskTracker.spawn`` once shutdown has begun; callers map it to 503.

    A dedicated type lets callers catch exactly this refusal, so an unrelated ``RuntimeError`` raised
    alongside a spawn (e.g. while preparing a resume) is not misreported as "shutting down".
    """


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback that surfaces an exception a fire-and-forget task would otherwise swallow.

    ``asyncio`` never reports the exception of a task nobody awaits, so without this a failed
    background task would leave only a "Task exception was never retrieved" warning at GC time. A
    ``WorkflowConfigError`` is an expected user-facing misconfiguration and is logged at ``warning``;
    anything else is unexpected and logged at ``error`` with a traceback.
    """
    if not task.cancelled() and (exc := task.exception()):
        if isinstance(exc, WorkflowConfigError):
            logger.warning("Background task aborted — configuration error: %s", exc)
        else:
            logger.error("Background task raised unhandled exception: %s", exc, exc_info=exc)


class BackgroundTaskTracker:
    """Starts, tracks and shuts down fire-and-forget asyncio tasks.

    All methods must run on the event loop the tasks belong to. Once ``shutdown`` has begun the
    tracker is terminal: it accepts no further ``spawn`` calls.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down = False

    def spawn(self, coro: Any, *, name: str) -> asyncio.Task[Any]:
        """Start ``coro`` as a tracked task and return it.

        Args:
            coro: The coroutine to run in the background.
            name: Task name, used only for debugging and ``repr``.

        Returns:
            The created ``asyncio.Task``.

        Raises:
            TrackerShutdownError: If ``shutdown`` has already begun. ``coro`` is closed first so it does not
                emit a "coroutine was never awaited" warning.
        """
        if self._shutting_down:
            coro.close()
            raise TrackerShutdownError("BackgroundTaskTracker is shutting down; refusing new task")
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        """Untrack a finished task and surface any exception it did not handle itself."""
        self._tasks.discard(task)
        _log_task_exception(task)

    @property
    def tasks(self) -> frozenset[asyncio.Task[Any]]:
        """Return the currently tracked tasks without exposing the mutable backing set."""
        return frozenset(self._tasks)

    async def shutdown(self, *, budget: timedelta = FINALIZE_BUDGET) -> int:
        """Stop accepting new tasks, cancel every tracked one, and wait up to ``budget`` for them.

        Cancellation is cooperative: a leased task finalizes its own run in its ``on_cancelled`` hook
        before exiting. A task that ignores cancellation, or that outlives the budget, is left running.

        Args:
            budget: How long to wait for the cancelled tasks to finish before giving up.

        Returns:
            The number of tasks still running when the budget elapsed. These are not abandoned — the
            lease reaper finalizes their runs once the lease expires.
        """
        self._shutting_down = True
        tasks = list(self._tasks)
        if not tasks:
            return 0
        for task in tasks:
            task.cancel()
        _done, pending = await asyncio.wait(tasks, timeout=budget.total_seconds())
        if pending:
            logger.warning(
                "BackgroundTaskTracker shutdown: %d task(s) still running after %.0fs; the reaper will finalize them",
                len(pending),
                budget.total_seconds(),
            )
        return len(pending)
