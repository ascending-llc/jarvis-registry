"""Bounded concurrent execution with per-item failure isolation."""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from types import TracebackType


@dataclass(frozen=True, slots=True)
class BoundedResult[T, R]:
    """One item's outcome from :func:`run_bounded`.

    ``result`` may legitimately be ``None`` for successful handlers, so callers
    must use :attr:`ok` rather than checking the result value.
    """

    item: T
    result: R | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def exc_info(self) -> tuple[type[Exception], Exception, TracebackType | None] | None:
        """Return a logging-compatible exception tuple for captured failures."""
        if self.error is None:
            return None
        return type(self.error), self.error, self.error.__traceback__


async def run_bounded[T, R](
    items: Iterable[T],
    handler: Callable[[T], Awaitable[R]],
    *,
    limit: int,
) -> list[BoundedResult[T, R]]:
    """Run ``handler`` for each item with at most ``limit`` calls in flight.

    Handler failures are returned with their input items instead of cancelling
    sibling work. Results retain input order. Task cancellation is deliberately
    not captured because :class:`asyncio.CancelledError` is a ``BaseException``.
    """
    if limit < 1:
        raise ValueError("Concurrency limit must be greater than zero")

    materialized_items = tuple(items)
    if not materialized_items:
        return []

    semaphore = asyncio.Semaphore(limit)

    async def _run_one(item: T) -> BoundedResult[T, R]:
        async with semaphore:
            try:
                return BoundedResult(item=item, result=await handler(item))
            except Exception as exc:  # noqa: BLE001 - isolate this item without cancelling siblings
                return BoundedResult(item=item, error=exc)

    return list(await asyncio.gather(*(_run_one(item) for item in materialized_items)))
