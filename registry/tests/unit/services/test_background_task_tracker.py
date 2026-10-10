import asyncio
from datetime import timedelta

import pytest

from registry.services.background_task_tracker import BackgroundTaskTracker


@pytest.mark.asyncio
async def test_spawn_runs_and_tracks_task() -> None:
    tracker = BackgroundTaskTracker()
    ran = asyncio.Event()

    async def work() -> None:
        ran.set()

    task = tracker.spawn(work(), name="w")
    await task
    assert ran.is_set()


@pytest.mark.asyncio
async def test_spawn_refused_after_shutdown_and_coro_closed() -> None:
    tracker = BackgroundTaskTracker()
    await tracker.shutdown()

    started = False

    async def work() -> None:
        nonlocal started
        started = True

    coro = work()
    with pytest.raises(RuntimeError, match="shutting down"):
        tracker.spawn(coro, name="late")
    # The refused coroutine was closed, so it neither runs nor warns "never awaited".
    assert started is False


@pytest.mark.asyncio
async def test_shutdown_cancels_running_task() -> None:
    tracker = BackgroundTaskTracker()
    cancelled = asyncio.Event()

    async def forever() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    tracker.spawn(forever(), name="forever")
    await asyncio.sleep(0)  # let it start

    pending = await tracker.shutdown(budget=timedelta(seconds=1))
    assert pending == 0
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_shutdown_reports_tasks_that_outlast_budget() -> None:
    tracker = BackgroundTaskTracker()
    release = asyncio.Event()

    async def stubborn() -> None:
        # Swallow cancellation so the task is still pending when the budget elapses, but exit
        # once ``release`` is set so the test can clean the task up afterwards.
        while not release.is_set():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                continue

    task = tracker.spawn(stubborn(), name="stubborn")
    await asyncio.sleep(0)

    pending = await tracker.shutdown(budget=timedelta(seconds=0.05))
    assert pending == 1

    release.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_only_cancels_tracked_tasks() -> None:
    """Regression guard for the old cluster-wide sweep: shutdown must touch only the tasks this
    tracker started, never unrelated work running elsewhere on the loop."""
    tracker = BackgroundTaskTracker()
    tracked_cancelled = asyncio.Event()
    foreign_ran_to_completion = asyncio.Event()

    async def tracked() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            tracked_cancelled.set()
            raise

    async def foreign() -> None:
        # Not registered with the tracker — must survive its shutdown untouched.
        await asyncio.sleep(0.05)
        foreign_ran_to_completion.set()

    tracker.spawn(tracked(), name="tracked")
    foreign_task = asyncio.create_task(foreign())
    await asyncio.sleep(0)

    pending = await tracker.shutdown(budget=timedelta(seconds=1))
    assert pending == 0
    assert tracked_cancelled.is_set()  # our task was cancelled

    await foreign_task  # the untracked task was never cancelled and finished normally
    assert foreign_ran_to_completion.is_set()
