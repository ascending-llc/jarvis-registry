import asyncio

import pytest

from registry.services import embedding_maintenance_watcher as watcher_mod
from registry.services.embedding_maintenance_watcher import (
    EmbeddingMaintenanceWatcher,
    raise_if_reindex_active,
)
from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException


def test_is_active_defaults_to_false() -> None:
    assert EmbeddingMaintenanceWatcher().is_active() is False


def test_raise_if_reindex_active_none_watcher_is_noop() -> None:
    # Unwired watcher (e.g. in a unit test) is treated as not active.
    raise_if_reindex_active(None)


def test_raise_if_reindex_active_raises_when_active() -> None:
    watcher = EmbeddingMaintenanceWatcher()
    watcher._active = True
    with pytest.raises(EmbeddingReindexInProgressException):
        raise_if_reindex_active(watcher)


def test_raise_if_reindex_active_passes_when_inactive() -> None:
    raise_if_reindex_active(EmbeddingMaintenanceWatcher())


@pytest.mark.asyncio
async def test_poll_sets_active_from_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watcher_mod, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", lambda: _async(object()))

    watcher = EmbeddingMaintenanceWatcher()
    await watcher.start()
    try:
        await _wait_until(lambda: watcher.is_active(), timeout=1.0)
        assert watcher.is_active() is True
    finally:
        await watcher.shutdown()


@pytest.mark.asyncio
async def test_poll_clears_active_when_no_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watcher_mod, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", lambda: _async(None))

    watcher = EmbeddingMaintenanceWatcher()
    watcher._active = True
    await watcher.start()
    try:
        await _wait_until(lambda: watcher.is_active() is False, timeout=1.0)
        assert watcher.is_active() is False
    finally:
        await watcher.shutdown()


@pytest.mark.asyncio
async def test_poll_error_does_not_crash_watcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watcher_mod, "_POLL_INTERVAL_SECONDS", 0.01)

    async def _boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", _boom)

    watcher = EmbeddingMaintenanceWatcher()
    await watcher.start()
    try:
        await asyncio.sleep(0.05)
        # A failing poll must not flip the gate on and must not kill the task.
        assert watcher.is_active() is False
        assert watcher._task is not None and not watcher._task.done()
    finally:
        await watcher.shutdown()


@pytest.mark.asyncio
async def test_shutdown_is_safe_without_start() -> None:
    await EmbeddingMaintenanceWatcher().shutdown()


async def _async(value):
    return value


async def _wait_until(predicate, *, timeout: float) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")
