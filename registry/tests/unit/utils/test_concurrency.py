"""Tests for bounded concurrent execution helpers."""

import asyncio

import pytest

from registry.utils.concurrency import run_bounded


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_bounded_respects_limit_and_preserves_input_order() -> None:
    in_flight = 0
    peak_in_flight = 0
    release = asyncio.Event()
    first_pair_started = asyncio.Event()

    async def _handler(item: int) -> int:
        nonlocal in_flight, peak_in_flight
        in_flight += 1
        peak_in_flight = max(peak_in_flight, in_flight)
        if in_flight == 2:
            first_pair_started.set()
        await release.wait()
        in_flight -= 1
        return item * 10

    task = asyncio.create_task(run_bounded(range(4), _handler, limit=2))
    await asyncio.wait_for(first_pair_started.wait(), timeout=1)
    assert peak_in_flight == 2

    release.set()
    outcomes = await task

    assert peak_in_flight == 2
    assert [outcome.item for outcome in outcomes] == [0, 1, 2, 3]
    assert [outcome.result for outcome in outcomes] == [0, 10, 20, 30]
    assert all(outcome.ok for outcome in outcomes)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_bounded_isolates_failure_and_attempts_every_item() -> None:
    attempted: list[int] = []

    async def _handler(item: int) -> int:
        attempted.append(item)
        if item == 2:
            raise RuntimeError("broken item")
        return item

    outcomes = await run_bounded([1, 2, 3], _handler, limit=2)

    assert sorted(attempted) == [1, 2, 3]
    assert outcomes[0].result == 1
    assert isinstance(outcomes[1].error, RuntimeError)
    assert not outcomes[1].ok
    assert outcomes[1].exc_info is not None
    assert outcomes[1].exc_info[1] is outcomes[1].error
    assert outcomes[2].result == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_bounded_treats_none_result_as_success() -> None:
    async def _handler(_item: str) -> None:
        return None

    [outcome] = await run_bounded(["item"], _handler, limit=1)

    assert outcome.ok
    assert outcome.result is None
    assert outcome.error is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_bounded_returns_empty_result_without_calling_handler() -> None:
    called = False

    async def _handler(_item: object) -> None:
        nonlocal called
        called = True

    assert await run_bounded([], _handler, limit=1) == []
    assert not called


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1])
async def test_run_bounded_rejects_non_positive_limit(limit: int) -> None:
    async def _handler(item: int) -> int:
        return item

    with pytest.raises(ValueError, match="greater than zero"):
        await run_bounded([1], _handler, limit=limit)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_bounded_propagates_cancellation() -> None:
    started = asyncio.Event()

    async def _handler(_item: int) -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(run_bounded([1], _handler, limit=1))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
