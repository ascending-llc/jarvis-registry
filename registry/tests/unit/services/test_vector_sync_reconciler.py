"""Unit tests for the leader-leased vector-sync reconciler (pass logic only; the lease takeover is
covered by the real-Mongo integration test)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from beanie import PydanticObjectId

from registry.services import vector_sync_reconciler as vsr
from registry.services.vector_sync_reconciler import VectorSyncReconciler
from registry_pkgs.models import ExtendedMCPServer

pytestmark = pytest.mark.asyncio


def _make_reconciler(*, reindex_active: bool = False) -> VectorSyncReconciler:
    watcher = Mock()
    watcher.is_active.return_value = reindex_active
    return VectorSyncReconciler(
        mcp_server_repo=Mock(),
        a2a_agent_repo=Mock(),
        embedding_maintenance_watcher=watcher,
        lease_owner="owner-1",
    )


def _doc(marker: datetime, attempts: int = 0):
    return SimpleNamespace(id=PydanticObjectId(), vectorSyncPendingAt=marker, vectorSyncAttempts=attempts)


def _patch_find(model, docs):
    chain = Mock()
    chain.sort.return_value = chain
    chain.limit.return_value = chain
    chain.to_list = AsyncMock(return_value=docs)
    return patch.object(model, "find", Mock(return_value=chain))


def _patch_collection(model):
    collection = Mock()
    collection.update_one = AsyncMock()
    return patch.object(model, "get_pymongo_collection", return_value=collection), collection


async def test_reconcile_clears_marker_on_success():
    rec = _make_reconciler()
    now = datetime.now(UTC)
    doc = _doc(now - timedelta(minutes=5))
    sync_fn = AsyncMock(return_value=True)
    with _patch_find(ExtendedMCPServer, [doc]), patch.object(vsr, "sync_pending_vectors", sync_fn):
        synced, failed, full = await rec._reconcile(ExtendedMCPServer, rec._mcp_server_repo, now)

    assert (synced, failed, full) == (1, 0, False)
    sync_fn.assert_awaited_once_with(ExtendedMCPServer, rec._mcp_server_repo, doc.id)


async def test_reconcile_requeues_with_backoff_on_failure():
    rec = _make_reconciler()
    now = datetime.now(UTC)
    doc = _doc(now - timedelta(minutes=5), attempts=3)
    sync_fn = AsyncMock(side_effect=RuntimeError("weaviate down"))
    coll_patch, collection = _patch_collection(ExtendedMCPServer)
    with _patch_find(ExtendedMCPServer, [doc]), coll_patch, patch.object(vsr, "sync_pending_vectors", sync_fn):
        synced, failed, _ = await rec._reconcile(ExtendedMCPServer, rec._mcp_server_repo, now)

    assert (synced, failed) == (0, 1)
    args, _kwargs = collection.update_one.await_args
    # compare-and-set on the loaded marker; attempts=3 -> 60s * 2**3 = 480s
    assert args[0] == {"_id": doc.id, "vectorSyncPendingAt": doc.vectorSyncPendingAt}
    assert args[1]["$set"]["vectorSyncPendingAt"] == now + timedelta(seconds=480)
    assert args[1]["$inc"] == {"vectorSyncAttempts": 1}


async def test_requeue_caps_backoff_at_max():
    now = datetime.now(UTC)
    doc = _doc(now, attempts=100)
    coll_patch, collection = _patch_collection(ExtendedMCPServer)
    with coll_patch:
        await VectorSyncReconciler._requeue(ExtendedMCPServer, doc, now)
    args, _ = collection.update_one.await_args
    assert args[1]["$set"]["vectorSyncPendingAt"] == now + timedelta(hours=6)


async def test_run_pass_skips_while_reindex_active():
    rec = _make_reconciler(reindex_active=True)
    with _patch_find(ExtendedMCPServer, [_doc(datetime.now(UTC))]) as find:
        full = await rec._run_pass()
    assert full is False
    find.assert_not_called()


async def test_reconcile_reports_full_batch():
    rec = _make_reconciler()
    now = datetime.now(UTC)
    docs = [_doc(now - timedelta(minutes=5)) for _ in range(vsr._BATCH)]
    sync_fn = AsyncMock(return_value=True)
    with _patch_find(ExtendedMCPServer, docs), patch.object(vsr, "sync_pending_vectors", sync_fn):
        _, _, full = await rec._reconcile(ExtendedMCPServer, rec._mcp_server_repo, now)
    assert full is True


async def test_execute_releases_with_zero_interval_on_full_batch():
    rec = _make_reconciler()
    lease = Mock()
    with (
        patch.object(rec, "_run_pass", AsyncMock(return_value=True)),
        patch.object(vsr, "release_leader", AsyncMock()) as release,
    ):
        await rec._execute(Mock(), lease)
    release.assert_awaited_once_with(rec._repo, lease, interval=timedelta(0))


async def test_execute_releases_with_pass_interval_when_not_full():
    rec = _make_reconciler()
    lease = Mock()
    with (
        patch.object(rec, "_run_pass", AsyncMock(return_value=False)),
        patch.object(vsr, "release_leader", AsyncMock()) as release,
    ):
        await rec._execute(Mock(), lease)
    release.assert_awaited_once_with(rec._repo, lease, interval=vsr._PASS_INTERVAL)


async def test_execute_releases_even_when_pass_raises():
    rec = _make_reconciler()
    lease = Mock()
    with (
        patch.object(rec, "_run_pass", AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(vsr, "release_leader", AsyncMock()) as release,
    ):
        with pytest.raises(RuntimeError):
            await rec._execute(Mock(), lease)
    release.assert_awaited_once_with(rec._repo, lease, interval=vsr._PASS_INTERVAL)
