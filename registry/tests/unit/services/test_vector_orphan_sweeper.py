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
from registry.services.vector_sync_helpers import OrphanSweepGuardError
from registry_pkgs.database.leased_job import Lease

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
        max_orphan_fraction=vos._GUARD_MAX_ORPHAN_FRACTION,
        guard_min_total=vos._GUARD_MIN_TOTAL,
    )


async def test_pass_aborted_by_guard_reschedules_normally():
    """A tripped safety guard deletes nothing, logs, and reschedules at the hourly interval."""
    sweeper = _make_sweeper()
    trip = AsyncMock(side_effect=OrphanSweepGuardError("server: 25/30 look orphaned (> 50%)"))
    with patch.object(vos, "delete_orphan_vectors", trip):
        interval = await sweeper._run_pass()

    assert interval == _SWEEP_INTERVAL
    # The guard params are forwarded so the breaker can trip.
    assert trip.await_args.kwargs["max_orphan_fraction"] == vos._GUARD_MAX_ORPHAN_FRACTION
    assert trip.await_args.kwargs["guard_min_total"] == vos._GUARD_MIN_TOTAL


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
