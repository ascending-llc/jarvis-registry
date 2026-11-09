"""Unit tests for the leader-leased vector orphan sweeper (pass logic and release scheduling)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from registry.services import vector_orphan_sweeper as vos
from registry.services.vector_orphan_sweeper import (
    _REINDEX_RETRY_INTERVAL,
    _SWEEP_INTERVAL,
    VectorOrphanSweeper,
)
from registry_pkgs.database.leased_job import Lease
from registry_pkgs.models import LeaderLease
from registry_pkgs.models.leader_lease import leader_claimable_filter

pytestmark = pytest.mark.asyncio


def _make_sweeper(*, reindex_active: bool = False) -> VectorOrphanSweeper:
    watcher = Mock()
    watcher.is_active.return_value = reindex_active
    mcp_repo = Mock()
    mcp_repo.alist_entity_ids = AsyncMock(return_value={"s1", "s2"})
    a2a_repo = Mock()
    a2a_repo.alist_entity_ids = AsyncMock(return_value={"a1"})
    return VectorOrphanSweeper(
        mcp_server_repo=mcp_repo,
        a2a_agent_repo=a2a_repo,
        embedding_maintenance_watcher=watcher,
        lease_owner="owner-1",
    )


def _lease() -> Lease:
    return Lease(doc_id="x", owner="owner-1", token="tok", expires_at=datetime.now(UTC))


async def test_pass_deletes_orphans_via_shared_helper():
    sweeper = _make_sweeper()
    delete = AsyncMock(return_value=(1, 0))
    with patch.object(vos, "delete_orphan_vectors", delete):
        interval = await sweeper._run_pass()

    assert interval == _SWEEP_INTERVAL
    # Vector ids are read before the helper reads Mongo.
    sweeper._mcp_server_repo.alist_entity_ids.assert_awaited_once()
    sweeper._a2a_agent_repo.alist_entity_ids.assert_awaited_once()
    delete.assert_awaited_once_with(
        sweeper._mcp_server_repo,
        sweeper._a2a_agent_repo,
        vector_mcp_ids={"s1", "s2"},
        vector_a2a_ids={"a1"},
    )


async def test_pass_skipped_and_rescheduled_soon_during_reindex():
    sweeper = _make_sweeper(reindex_active=True)
    delete = AsyncMock()
    with patch.object(vos, "delete_orphan_vectors", delete):
        interval = await sweeper._run_pass()

    assert interval == _REINDEX_RETRY_INTERVAL == timedelta(minutes=5)
    delete.assert_not_awaited()
    # Collections are not enumerated while the reindex owns them.
    sweeper._mcp_server_repo.alist_entity_ids.assert_not_awaited()


async def test_execute_releases_one_hour_out_after_a_pass():
    sweeper = _make_sweeper()
    release = AsyncMock()
    with (
        patch.object(vos, "delete_orphan_vectors", AsyncMock(return_value=(0, 0))),
        patch.object(vos, "release_leader", release),
    ):
        await sweeper._execute(Mock(), _lease())

    # nextRunAt is stamped now + _SWEEP_INTERVAL (~1h), so a second owner polling immediately can't
    # claim the leader document until then (leader_claimable_filter requires nextRunAt <= now).
    release.assert_awaited_once()
    assert release.await_args.kwargs["interval"] == _SWEEP_INTERVAL


async def test_execute_releases_even_when_pass_raises():
    sweeper = _make_sweeper()
    release = AsyncMock()
    with (
        patch.object(vos, "delete_orphan_vectors", AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(vos, "release_leader", release),
        pytest.raises(RuntimeError),
    ):
        await sweeper._execute(Mock(), _lease())

    release.assert_awaited_once()
    assert release.await_args.kwargs["interval"] == _SWEEP_INTERVAL


class _OneDocCollection:
    """In-memory stand-in for ``registry_leader_leases`` holding one document.

    Supports exactly the query shapes the leader lease uses (equality incl. ``None``, ``$lte``, ``$gt``,
    ``$in``, ``$and``, ``$or``) and ``$set`` updates, so the real ``claim`` / ``release_leader`` /
    ``leader_claimable_filter`` run unmocked against it.
    """

    def __init__(self, doc: dict):
        self.doc = doc

    @classmethod
    def _matches(cls, doc: dict, query: dict) -> bool:
        for key, cond in query.items():
            if key == "$and":
                if not all(cls._matches(doc, q) for q in cond):
                    return False
            elif key == "$or":
                if not any(cls._matches(doc, q) for q in cond):
                    return False
            elif isinstance(cond, dict):
                value = doc.get(key)
                for op, operand in cond.items():
                    if op == "$lte" and not (value is not None and value <= operand):
                        return False
                    if op == "$gt" and not (value is not None and value > operand):
                        return False
                    if op == "$in" and value not in operand:
                        return False
            elif doc.get(key) != cond:
                return False
        return True

    async def find_one_and_update(self, query, update, **_kwargs):
        if not self._matches(self.doc, query):
            return None
        self.doc.update(update["$set"])
        return dict(self.doc)

    async def update_one(self, query, update, **_kwargs):
        matched = self._matches(self.doc, query)
        if matched:
            self.doc.update(update["$set"])
        return Mock(matched_count=int(matched))


@pytest.mark.parametrize(
    ("reindex_active", "expected_interval"),
    [(False, _SWEEP_INTERVAL), (True, _REINDEX_RETRY_INTERVAL)],
)
async def test_released_lease_is_not_claimable_until_next_run_at(reindex_active, expected_interval):
    """After a pass, ``nextRunAt`` is ~interval ahead, and a second owner polling now can't claim it."""

    collection = _OneDocCollection({"_id": vos._LEASE_NAME, "status": "active"})
    first = _make_sweeper(reindex_active=reindex_active)
    second = _make_sweeper(reindex_active=reindex_active)

    with (
        patch.object(LeaderLease, "get_pymongo_collection", return_value=collection),
        patch.object(vos, "delete_orphan_vectors", AsyncMock(return_value=(0, 0))),
    ):
        claimed = await first._claim("owner-1")
        assert claimed is not None
        before = datetime.now(UTC)
        await first._execute(*claimed)
        after = datetime.now(UTC)

        # Released, and the next pass is scheduled one interval out.
        assert collection.doc["leaseToken"] is None
        assert before + expected_interval <= collection.doc["nextRunAt"] <= after + expected_interval

        # Another pod polling right away finds the lease free but not due.
        assert await second._claim("owner-2") is None
        # Once the interval has elapsed it is claimable again.
        due = collection.doc["nextRunAt"] + timedelta(seconds=1)
        reclaimed = await second._repo.claim(
            owner="owner-2", claimable_filter=leader_claimable_filter(vos._LEASE_NAME, due)
        )
        assert reclaimed is not None
        assert collection.doc["leaseOwner"] == "owner-2"
