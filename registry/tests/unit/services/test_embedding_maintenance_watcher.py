import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import embedding_maintenance_watcher as watcher_mod
from registry.services.embedding_maintenance_watcher import (
    EmbeddingMaintenanceWatcher,
    gc_stale_embedding_generations,
    raise_if_reindex_active,
)
from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.models.enums import EmbeddingReindexJobStatus

pytestmark = pytest.mark.asyncio


class _AList:
    """Minimal async-iterable for a mocked ``find()`` cursor."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        async def _gen():
            for item in self._items:
                yield item

        return _gen()


def _wire_gc(monkeypatch, *, active, this_pod="__active__", running_prev=(), recent_prev=(), jobs=None, collections=()):
    """Wire gc_stale_embedding_generations' collaborators; returns (db_client, dropped list)."""
    monkeypatch.setattr(
        watcher_mod,
        "get_model_gateway_selection",
        AsyncMock(return_value=SimpleNamespace(embeddingCollectionGeneration=active)),
    )
    running_docs = [{"previousCollectionGeneration": g} for g in running_prev]

    async def _find_one(query):
        return {"_id": "k"} if query.get("previousCollectionGeneration") in recent_prev else None

    monkeypatch.setattr(
        watcher_mod.EmbeddingReindexJob,
        "get_pymongo_collection",
        lambda: SimpleNamespace(find=lambda q: _AList(running_docs), find_one=_find_one),
    )
    jobs = jobs or {}
    monkeypatch.setattr(watcher_mod.EmbeddingReindexJob, "get", AsyncMock(side_effect=lambda oid: jobs.get(str(oid))))

    dropped: list[str] = []
    adapter = SimpleNamespace(list_collections=lambda: list(collections), drop_collection=lambda n: dropped.append(n))
    pod_gen = active if this_pod == "__active__" else this_pod
    return SimpleNamespace(adapter=adapter, collection_generation=pod_gen), dropped


def _finished_job(status=EmbeddingReindexJobStatus.COMPLETED):
    return SimpleNamespace(status=status)


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


async def test_gc_drops_when_all_conditions_hold(monkeypatch) -> None:
    active = str(PydanticObjectId())
    orphan = str(PydanticObjectId())  # a finished job's generation, safe to drop
    db_client, dropped = _wire_gc(
        monkeypatch,
        active=active,
        collections=[
            "MCP_Servers",  # base name — never dropped
            f"MCP_Servers_{active}",  # active — kept (b)
            f"MCP_Servers_{orphan}",  # finished, unprotected — dropped
            f"A2a_agents_{orphan}",  # finished, unprotected — dropped
            "SomethingElse",  # not ours — ignored
        ],
        jobs={orphan: _finished_job(EmbeddingReindexJobStatus.FAILED)},
    )

    await gc_stale_embedding_generations(db_client)

    assert set(dropped) == {f"MCP_Servers_{orphan}", f"A2a_agents_{orphan}"}


async def test_gc_a_keeps_generation_of_a_running_or_missing_job(monkeypatch) -> None:
    active = str(PydanticObjectId())
    still_running = str(PydanticObjectId())  # its own job is RUNNING -> (a) fails
    no_job = str(PydanticObjectId())  # no job document -> not ours
    db_client, dropped = _wire_gc(
        monkeypatch,
        active=active,
        collections=[f"MCP_Servers_{still_running}", f"MCP_Servers_{no_job}"],
        jobs={still_running: _finished_job(EmbeddingReindexJobStatus.RUNNING)},  # not COMPLETED/FAILED
    )

    await gc_stale_embedding_generations(db_client)

    assert dropped == []


async def test_gc_c_keeps_previous_generation_of_a_running_job(monkeypatch) -> None:
    active = str(PydanticObjectId())
    prev = str(PydanticObjectId())  # a RUNNING job's previousCollectionGeneration
    db_client, dropped = _wire_gc(
        monkeypatch,
        active=active,
        running_prev=[prev],  # protected by (c) even though its own job is finished
        collections=[f"MCP_Servers_{prev}"],
        jobs={prev: _finished_job(EmbeddingReindexJobStatus.COMPLETED)},
    )

    await gc_stale_embedding_generations(db_client)

    assert dropped == []


async def test_gc_d_keeps_recently_superseded_previous_generation(monkeypatch) -> None:
    active = str(PydanticObjectId())
    prev = str(PydanticObjectId())  # previous of a COMPLETED job still within the retention window
    db_client, dropped = _wire_gc(
        monkeypatch,
        active=active,
        recent_prev=[prev],  # (d): a lagging pod may still read it
        collections=[f"MCP_Servers_{prev}"],
        jobs={prev: _finished_job(EmbeddingReindexJobStatus.COMPLETED)},
    )

    await gc_stale_embedding_generations(db_client)

    assert dropped == []


async def test_gc_skips_on_a_stale_pod(monkeypatch) -> None:
    # A pod not itself on the active generation still reads the previous one and must not GC at all.
    active = str(PydanticObjectId())
    orphan = str(PydanticObjectId())
    db_client, dropped = _wire_gc(
        monkeypatch,
        active=active,
        this_pod=str(PydanticObjectId()),  # behind the active generation
        collections=[f"MCP_Servers_{orphan}"],
        jobs={orphan: _finished_job(EmbeddingReindexJobStatus.COMPLETED)},
    )

    await gc_stale_embedding_generations(db_client)

    assert dropped == []


async def test_gc_uses_to_thread_for_weaviate_calls(monkeypatch) -> None:
    active = str(PydanticObjectId())
    orphan = str(PydanticObjectId())
    db_client, dropped = _wire_gc(
        monkeypatch,
        active=active,
        collections=[f"MCP_Servers_{orphan}"],
        jobs={orphan: _finished_job(EmbeddingReindexJobStatus.COMPLETED)},
    )
    calls: list = []
    real_to_thread = watcher_mod.asyncio.to_thread

    async def _tracking(func, *args):
        calls.append(func)
        return await real_to_thread(func, *args)

    monkeypatch.setattr(watcher_mod.asyncio, "to_thread", _tracking)

    await gc_stale_embedding_generations(db_client)

    assert db_client.adapter.list_collections in calls
    assert db_client.adapter.drop_collection in calls
    assert dropped == [f"MCP_Servers_{orphan}"]
