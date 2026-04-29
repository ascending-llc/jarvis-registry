"""
In-process directive signal bus for live workflow runs.

Each active WorkflowRun gets a dedicated ``asyncio.Queue`` registered here for
its lifetime.  The API layer calls :meth:`DirectiveQueue.put` to send a
directive; the executor wrapper calls :meth:`DirectiveQueue.get_nowait` (or
:meth:`DirectiveQueue.wait_for_directive` when paused) to receive it.

Design notes
------------
* ``asyncio.Queue`` is the *fast path*: zero-latency, zero extra I/O.
* ``WorkflowRun.pending_directive`` in MongoDB is the *source of truth*: it
  survives service restarts and acts as a fallback when a directive is routed
  to a different replica.  The executor wrapper reads MongoDB when the Queue is
  empty (see :mod:`registry_pkgs.workflows.control.wrapper`).
* Queue entries are consumed (get_nowait / wait_for_directive) so a directive
  is processed exactly once by the waiting coroutine.  The MongoDB field is
  cleared separately by WorkflowControlService after the transition is persisted.
"""

from __future__ import annotations

import asyncio
import logging

from registry_pkgs.models.enums import WorkflowDirective

logger = logging.getLogger(__name__)


class DirectiveQueue:
    """Per-run asyncio.Queue registry.

    Intended to be instantiated **once** per application process and shared
    via FastAPI dependency injection or app state.

    Lifecycle
    ---------
    1. ``register(run_id)``   — called by WorkflowRunner when a run starts.
    2. ``put(run_id, d)``     — called by the API handler after persisting the
                                directive to MongoDB.
    3. ``get_nowait(run_id)`` — called by the executor wrapper before each step.
    4. ``wait_for_directive`` — called by the executor wrapper while paused.
    5. ``unregister(run_id)`` — called in the runner's ``finally`` block when
                                the run finishes (success, failure, or cancel).
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[WorkflowDirective]] = {}

    def register(self, run_id: str) -> None:
        """Create a fresh Queue for *run_id*.

        Safe to call multiple times; subsequent calls are no-ops so that
        re-entrant code paths do not accidentally reset a queue that already
        holds a directive.
        """
        if run_id not in self._queues:
            self._queues[run_id] = asyncio.Queue()
            logger.debug("DirectiveQueue: registered run %s", run_id)

    def unregister(self, run_id: str) -> None:
        """Remove the Queue for *run_id*. Safe to call on an unknown id (no-op)."""
        self._queues.pop(run_id, None)
        logger.debug("DirectiveQueue: unregistered run %s", run_id)

    def put(self, run_id: str, directive: WorkflowDirective) -> None:
        """Enqueue *directive* for *run_id* without blocking.

        If *run_id* has no registered Queue (run already finished and
        unregistered) the directive is silently dropped — the MongoDB write
        performed before this call is the durable record.
        """
        q = self._queues.get(run_id)
        if q is None:
            logger.debug(
                "DirectiveQueue.put: run %s has no queue (already finished), directive %s dropped",
                run_id,
                directive,
            )
            return
        q.put_nowait(directive)
        logger.debug("DirectiveQueue: queued %s for run %s", directive, run_id)

    def get_nowait(self, run_id: str) -> WorkflowDirective | None:
        """Return the next pending directive without blocking, or ``None``."""
        q = self._queues.get(run_id)
        if q is None:
            return None
        try:
            return q.get_nowait()
        except asyncio.QueueEmpty as exc:
            logger.debug(f"DirectiveQueue.get_nowait() raised {exc}")
            return None

    async def wait_for_directive(
        self,
        run_id: str,
        *,
        timeout: float,
    ) -> WorkflowDirective | None:
        """Await the next directive for *run_id*, returning ``None`` on timeout.

        Called by the executor wrapper while the step is paused.  The short
        ``timeout`` lets the wrapper interleave MongoDB fallback polls and
        pause-timeout enforcement without holding the Queue lock indefinitely.
        """
        q = self._queues.get(run_id)
        if q is None:
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except TimeoutError:
            return None
