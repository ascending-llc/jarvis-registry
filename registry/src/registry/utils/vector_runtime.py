"""Runtime helpers for scripts that share the registry container."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from registry.container import RegistryContainer

DEFAULT_VECTOR_WRITE_WAIT_SECONDS = 30.0


async def wait_for_vector_writes(
    container: RegistryContainer,
    *,
    timeout_seconds: float = DEFAULT_VECTOR_WRITE_WAIT_SECONDS,
) -> None:
    """Wait for vector-write background tasks already tracked by the container.

    Most maintenance scripts call vector repositories inline. This helper covers the cases where
    code paths schedule work through the app's shared background tracker before the script moves on.
    """
    tasks = list(container.background_task_tracker.tasks)
    if not tasks:
        await asyncio.sleep(0)
        return

    _done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
    if pending:
        raise TimeoutError(f"Timed out waiting for {len(pending)} vector write task(s) to finish")
