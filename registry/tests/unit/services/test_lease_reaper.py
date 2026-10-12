import asyncio
from datetime import timedelta

import pytest

from registry.services.lease_reaper import LeaseReaper


@pytest.mark.asyncio
async def test_loop_runs_every_task_each_tick() -> None:
    calls_a = 0
    calls_b = 0

    async def task_a() -> int:
        nonlocal calls_a
        calls_a += 1
        return 0

    async def task_b() -> int:
        nonlocal calls_b
        calls_b += 1
        return 2

    reaper = LeaseReaper([("a", task_a), ("b", task_b)], interval=timedelta(seconds=0.01))
    await reaper.start()
    await asyncio.sleep(0.05)
    await reaper.shutdown()

    assert calls_a >= 1
    assert calls_b >= 1


@pytest.mark.asyncio
async def test_one_failing_task_does_not_stop_the_others() -> None:
    good_calls = 0

    async def boom() -> int:
        raise RuntimeError("reap failed")

    async def good() -> int:
        nonlocal good_calls
        good_calls += 1
        return 0

    reaper = LeaseReaper([("boom", boom), ("good", good)], interval=timedelta(seconds=0.01))
    await reaper.start()
    await asyncio.sleep(0.05)
    await reaper.shutdown()

    assert good_calls >= 1  # the raising task was isolated; good still ran


@pytest.mark.asyncio
async def test_shutdown_stops_the_loop() -> None:
    calls = 0

    async def task() -> int:
        nonlocal calls
        calls += 1
        return 0

    reaper = LeaseReaper([("t", task)], interval=timedelta(seconds=0.01))
    await reaper.start()
    await asyncio.sleep(0.03)
    await reaper.shutdown()
    settled = calls
    await asyncio.sleep(0.03)
    assert calls == settled  # no further ticks after shutdown
