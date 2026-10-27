"""Unit tests for the immediate (post-commit) vector-sync scheduling shared shape: tasks go through
the tracker, clear the marker only on success, and tolerate a tracker that is shutting down."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.a2a_agent_service import A2AAgentService
from registry.services.background_task_tracker import BackgroundTaskTracker
from registry.services.server_service import ServerServiceV1
from registry.services.vector_sync_helpers import VectorSyncPlan

pytestmark = pytest.mark.asyncio

_LEASE_OWNER = "registry-pod-1"


class _CapturingTracker:
    """Records the coroutine instead of scheduling it, so the test can run it deterministically."""

    def __init__(self):
        self.coro = None
        self.name = None

    def spawn(self, coro, *, name):
        self.coro = coro
        self.name = name
        return Mock()


def _server_service(tracker):
    return ServerServiceV1(
        user_service=Mock(),
        token_service=Mock(),
        oauth_service=Mock(),
        mcp_server_repo=Mock(),
        background_task_tracker=tracker,
        lease_owner=_LEASE_OWNER,
    )


def _agent_service(tracker):
    return A2AAgentService(
        a2a_agent_repo=Mock(), azure_client_cache=Mock(), background_task_tracker=tracker, lease_owner=_LEASE_OWNER
    )


@pytest.mark.parametrize("agent", [False, True])
async def test_scheduling_uses_tracked_shared_sync(agent):
    tracker = _CapturingTracker()
    service = _agent_service(tracker) if agent else _server_service(tracker)
    module = "a2a_agent_service" if agent else "server_service"
    plan = VectorSyncPlan(doc=Mock(id=PydanticObjectId()), marker=datetime.now(UTC), full_resync=True)
    with patch(f"registry.services.{module}.sync_pending_vectors", AsyncMock()) as sync:
        service.schedule_vector_sync(plan)
        assert tracker.name.endswith(str(plan.doc.id))
        sync.assert_not_awaited()
        await tracker.coro
    assert sync.await_args.kwargs["plan"] is plan
    assert sync.await_args.args[2] == plan.doc.id
    assert sync.await_args.kwargs["owner"] == _LEASE_OWNER  # per-entity lease is owned by this pod


async def test_sync_failure_is_logged_without_failing_committed_request(caplog):
    tracker = _CapturingTracker()
    service = _server_service(tracker)
    with patch("registry.services.server_service.sync_pending_vectors", AsyncMock(side_effect=RuntimeError("down"))):
        service.schedule_vector_sync(VectorSyncPlan(doc=Mock(id="s"), marker=datetime.now(UTC), full_resync=True))
        await tracker.coro
    assert "marker left for the reconciler" in caplog.text


async def test_missing_tracker_leaves_work_pending_without_untracked_task():
    service = _server_service(None)
    with patch("registry.services.server_service.asyncio.create_task") as spawn:
        service.schedule_vector_sync(VectorSyncPlan(doc=Mock(id="s"), marker=datetime.now(UTC), full_resync=True))
    spawn.assert_not_called()


async def test_server_schedule_after_tracker_shutdown_is_noop_and_does_not_raise():
    tracker = BackgroundTaskTracker()
    await tracker.shutdown()  # tracker now refuses spawns
    service = _server_service(tracker)
    service.mcp_server_repo.sync_to_vector_db = AsyncMock()
    # Must not raise: the write already committed, the reconciler heals the still-set marker.
    service.schedule_vector_sync(
        VectorSyncPlan(doc=Mock(id=PydanticObjectId()), marker=datetime.now(UTC), full_resync=True)
    )
    service.mcp_server_repo.sync_to_vector_db.assert_not_awaited()


async def test_no_repo_scheduling_is_noop():
    service = ServerServiceV1(user_service=Mock(), token_service=Mock(), oauth_service=Mock(), mcp_server_repo=None)
    service.schedule_vector_sync(VectorSyncPlan(doc=Mock(), marker=datetime.now(UTC), full_resync=True))
