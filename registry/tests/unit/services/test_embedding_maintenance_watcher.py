import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from registry.services import embedding_maintenance_watcher as watcher_mod
from registry.services.embedding_maintenance_watcher import (
    EmbeddingMaintenanceWatcher,
    gc_stale_embedding_generations,
    raise_if_reindex_active,
)
from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException

pytestmark = pytest.mark.asyncio


def test_is_active_defaults_to_false() -> None:
    assert EmbeddingMaintenanceWatcher().is_active() is False


def test_raise_if_reindex_active_none_watcher_is_noop() -> None:
    raise_if_reindex_active(None)


def test_raise_if_reindex_active_raises_when_job_active() -> None:
    watcher = EmbeddingMaintenanceWatcher()
    watcher._job_active = True
    with pytest.raises(EmbeddingReindexInProgressException):
        raise_if_reindex_active(watcher)


def test_raise_if_reindex_active_raises_when_pod_is_stale() -> None:
    # Even with no active job, a pod that has not yet swapped to the active generation blocks writes.
    watcher = EmbeddingMaintenanceWatcher()
    watcher._stale = True
    with pytest.raises(EmbeddingReindexInProgressException):
        raise_if_reindex_active(watcher)


def _patch_no_job(monkeypatch, active=False):
    monkeypatch.setattr(watcher_mod, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(
        watcher_mod, "get_active_embedding_reindex_job", AsyncMock(return_value=object() if active else None)
    )


async def test_unwired_watcher_tracks_only_job_active(monkeypatch) -> None:
    _patch_no_job(monkeypatch, active=True)
    watcher = EmbeddingMaintenanceWatcher()  # no db_client -> no swap, job tracking only
    await watcher._poll()
    assert watcher.is_active() is True


async def test_poll_swaps_adapter_when_generation_differs(monkeypatch) -> None:
    _patch_no_job(monkeypatch, active=False)
    target_config = SimpleNamespace(collection_generation="genB")
    monkeypatch.setattr(watcher_mod, "resolve_vector_backend_config", AsyncMock(return_value=target_config))
    new_adapter = object()
    monkeypatch.setattr(watcher_mod.VectorStoreFactory, "create_adapter", classmethod(lambda cls, cfg: new_adapter))

    db_client = MagicMock()
    db_client.collection_generation = "genA"  # this pod is behind
    old_adapter = object()
    db_client.swap_adapter = MagicMock(return_value=old_adapter)
    watcher = EmbeddingMaintenanceWatcher(db_client=db_client, settings=SimpleNamespace())

    await watcher._poll()

    db_client.swap_adapter.assert_called_once()
    assert db_client.swap_adapter.call_args.args[0] is new_adapter
    assert db_client.swap_adapter.call_args.args[1] is target_config
    assert watcher.is_active() is False  # swapped -> no longer stale
    assert [a for a, _ in watcher._retired] == [old_adapter]  # retired for deferred close


async def test_poll_stays_stale_and_retries_when_resolve_fails(monkeypatch) -> None:
    # A committed generation whose source is gone makes resolve fail-hard; the pod stays stale and
    # keeps blocking writes until the next poll rather than swapping onto a wrong config.
    _patch_no_job(monkeypatch, active=False)
    monkeypatch.setattr(
        watcher_mod, "resolve_vector_backend_config", AsyncMock(side_effect=RuntimeError("source gone"))
    )
    db_client = MagicMock()
    db_client.collection_generation = "genA"
    watcher = EmbeddingMaintenanceWatcher(db_client=db_client, settings=SimpleNamespace())

    await watcher._poll()

    db_client.swap_adapter.assert_not_called()
    assert watcher.is_active() is True  # still stale -> writes stay blocked, retried next poll


async def test_poll_no_swap_when_generation_matches(monkeypatch) -> None:
    # Legacy generation 0: no source committed -> resolve returns a config with generation None, which
    # matches this pod, so nothing swaps (and it does NOT error on the missing source).
    _patch_no_job(monkeypatch, active=False)
    monkeypatch.setattr(
        watcher_mod,
        "resolve_vector_backend_config",
        AsyncMock(return_value=SimpleNamespace(collection_generation=None)),
    )
    db_client = MagicMock()
    db_client.collection_generation = None  # matches target
    watcher = EmbeddingMaintenanceWatcher(db_client=db_client, settings=SimpleNamespace())

    await watcher._poll()

    db_client.swap_adapter.assert_not_called()
    assert watcher.is_active() is False


async def test_poll_error_does_not_crash_watcher(monkeypatch) -> None:
    monkeypatch.setattr(watcher_mod, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", AsyncMock(side_effect=RuntimeError("down")))
    watcher = EmbeddingMaintenanceWatcher()
    await watcher.start()
    try:
        await asyncio.sleep(0.05)
        assert watcher._task is not None and not watcher._task.done()
    finally:
        await watcher.shutdown()


async def test_shutdown_is_safe_without_start() -> None:
    await EmbeddingMaintenanceWatcher().shutdown()


async def test_retired_adapter_closed_only_after_grace(monkeypatch) -> None:
    closed: list[object] = []
    monkeypatch.setattr(watcher_mod, "_close_adapter", lambda a: closed.append(a))
    monkeypatch.setattr(watcher_mod, "_RETIRED_ADAPTER_GRACE_SECONDS", 100.0)
    watcher = EmbeddingMaintenanceWatcher()
    fresh, stale = object(), object()
    # fresh retired just now (kept); stale retired long ago (due to close).
    watcher._retired = [(fresh, time.monotonic()), (stale, time.monotonic() - 1000.0)]

    await watcher._close_expired_adapters()

    assert closed == [stale]
    assert [a for a, _ in watcher._retired] == [fresh]  # in-flight-safe one is kept


async def test_shutdown_closes_all_retired_adapters(monkeypatch) -> None:
    closed: list[object] = []
    monkeypatch.setattr(watcher_mod, "_close_adapter", lambda a: closed.append(a))
    monkeypatch.setattr(watcher_mod, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", AsyncMock(return_value=None))
    watcher = EmbeddingMaintenanceWatcher()
    a, b = object(), object()
    watcher._retired = [(a, time.monotonic()), (b, time.monotonic())]
    await watcher.start()

    await watcher.shutdown()

    assert set(closed) == {a, b}
    assert watcher._retired == []


async def test_maybe_gc_runs_only_after_the_interval(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(watcher_mod, "gc_stale_embedding_generations", AsyncMock(side_effect=lambda c: calls.append(c)))
    monkeypatch.setattr(watcher_mod, "_GC_INTERVAL_SECONDS", 100.0)
    db_client = SimpleNamespace()
    watcher = EmbeddingMaintenanceWatcher(db_client=db_client, settings=SimpleNamespace())

    # Anchor relative to now (not 0.0): on a freshly-booted host time.monotonic() can be < interval.
    watcher._last_gc = time.monotonic() - 1000.0  # older than the interval -> due now
    await watcher._maybe_gc()
    assert calls == [db_client]

    # Immediately after, it is within the interval and must not run again.
    await watcher._maybe_gc()
    assert calls == [db_client]


async def test_maybe_gc_noop_when_unwired() -> None:
    # No db_client (e.g. a unit-only watcher) -> nothing to GC.
    await EmbeddingMaintenanceWatcher()._maybe_gc()


async def test_gc_drops_only_stale_generation_collections(monkeypatch) -> None:
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", AsyncMock(return_value=None))
    selection = SimpleNamespace(embeddingCollectionGeneration="genB")
    monkeypatch.setattr(watcher_mod, "get_model_gateway_selection", AsyncMock(return_value=selection))
    dropped: list[str] = []
    adapter = SimpleNamespace(
        list_collections=lambda: [
            "MCP_Servers",  # legacy base — never dropped
            "MCP_Servers_genB",  # active — kept
            "MCP_Servers_genA",  # stale — dropped
            "A2a_agents_genA",  # stale — dropped
            "SomethingElse",  # not ours — ignored
        ],
        drop_collection=lambda name: dropped.append(name),
    )
    # This pod is on the active generation (genB), so it is allowed to GC.
    db_client = SimpleNamespace(adapter=adapter, collection_generation="genB")

    await gc_stale_embedding_generations(db_client)

    assert set(dropped) == {"MCP_Servers_genA", "A2a_agents_genA"}


async def test_gc_skips_on_a_stale_pod(monkeypatch) -> None:
    # This pod never swapped to the active generation (still on genA); it must not drop the
    # collection it is reading, even with no active job.
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", AsyncMock(return_value=None))
    monkeypatch.setattr(
        watcher_mod,
        "get_model_gateway_selection",
        AsyncMock(return_value=SimpleNamespace(embeddingCollectionGeneration="genB")),
    )
    dropped: list[str] = []
    adapter = SimpleNamespace(
        list_collections=lambda: ["MCP_Servers_genA", "MCP_Servers_genB"],
        drop_collection=lambda name: dropped.append(name),
    )
    db_client = SimpleNamespace(adapter=adapter, collection_generation="genA")  # behind

    await gc_stale_embedding_generations(db_client)

    assert dropped == []


async def test_gc_rechecks_active_job_right_before_dropping(monkeypatch) -> None:
    # No job at the first check, but a reindex commits before the drop (second check finds it):
    # nothing is dropped, so a still-in-grace previous generation is not deleted under a lagging pod.
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", AsyncMock(side_effect=[None, object()]))
    monkeypatch.setattr(
        watcher_mod,
        "get_model_gateway_selection",
        AsyncMock(return_value=SimpleNamespace(embeddingCollectionGeneration="genB")),
    )
    dropped: list[str] = []
    adapter = SimpleNamespace(
        list_collections=lambda: ["MCP_Servers_genA", "MCP_Servers_genB"],
        drop_collection=lambda name: dropped.append(name),
    )
    db_client = SimpleNamespace(adapter=adapter, collection_generation="genB")

    await gc_stale_embedding_generations(db_client)

    assert dropped == []


async def test_gc_skips_entirely_while_a_reindex_is_active(monkeypatch) -> None:
    # An active job may be sweeping into <base>_<jobId> or a pod may be lagging on the previous
    # generation; dropping anything now could corrupt the running reindex or a lagging read.
    monkeypatch.setattr(watcher_mod, "get_active_embedding_reindex_job", AsyncMock(return_value=object()))
    dropped: list[str] = []
    adapter = SimpleNamespace(
        list_collections=lambda: ["MCP_Servers_genA", "MCP_Servers_genB"],
        drop_collection=lambda name: dropped.append(name),
    )

    await gc_stale_embedding_generations(SimpleNamespace(adapter=adapter))

    assert dropped == []
