"""Unit tests for the shared per-entity vector-sync primitives."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.vector_sync_helpers import (
    VectorSyncPlan,
    clear_vector_sync_marker,
    sync_agent_vectors,
    sync_server_vectors,
)
from registry_pkgs.models import A2AAgent, ExtendedMCPServer

pytestmark = pytest.mark.asyncio


async def test_sync_server_vectors_ok():
    repo = Mock()
    repo.sync_to_vector_db = AsyncMock(return_value={"failed_tools": []})
    await sync_server_vectors(repo, Mock(), is_delete=True)
    repo.sync_to_vector_db.assert_awaited_once()


@pytest.mark.parametrize("result", [None, {"failed_tools": ["t"]}])
async def test_sync_server_vectors_raises_on_failure(result):
    repo = Mock()
    repo.sync_to_vector_db = AsyncMock(return_value=result)
    with pytest.raises(RuntimeError):
        await sync_server_vectors(repo, Mock(), is_delete=True)


@pytest.mark.parametrize("result", [None, {"failed": ["x"]}])
async def test_sync_agent_vectors_raises_on_failure(result):
    repo = Mock()
    repo.sync_to_vector_db = AsyncMock(return_value=result)
    with pytest.raises(RuntimeError):
        await sync_agent_vectors(repo, Mock(), is_delete=True)


async def test_clear_vector_sync_marker_compare_and_clears_by_value():
    marker = datetime.now(UTC)
    doc_id = PydanticObjectId()
    collection = Mock()
    collection.update_one = AsyncMock()
    with patch.object(ExtendedMCPServer, "get_pymongo_collection", return_value=collection):
        await clear_vector_sync_marker(ExtendedMCPServer, doc_id, marker)
    collection.update_one.assert_awaited_once_with(
        {"_id": doc_id, "vectorSyncPendingAt": marker},
        {"$unset": {"vectorSyncPendingAt": "", "vectorSyncAttempts": ""}},
    )


async def test_delete_entity_lease_removes_per_entity_lease_doc(monkeypatch):
    from registry.services.vector_sync_helpers import delete_entity_lease
    from registry_pkgs.models import LeaderLease

    doc_id = PydanticObjectId()
    collection = Mock(delete_one=AsyncMock())
    monkeypatch.setattr(ExtendedMCPServer, "get_collection_name", lambda: "mcpservers")
    monkeypatch.setattr(LeaderLease, "get_pymongo_collection", lambda: collection)
    await delete_entity_lease(ExtendedMCPServer, doc_id)
    collection.delete_one.assert_awaited_once_with({"_id": f"vector-entity:mcpservers:{doc_id}"})


async def test_vector_sync_plan_is_frozen():
    from dataclasses import FrozenInstanceError

    plan = VectorSyncPlan(doc=Mock(), marker=datetime.now(UTC), full_resync=True)
    with pytest.raises(FrozenInstanceError):
        plan.full_resync = False  # type: ignore[misc]


async def test_models_carry_marker_fields():
    for model in (ExtendedMCPServer, A2AAgent):
        assert "vectorSyncPendingAt" in model.model_fields
        assert "vectorSyncAttempts" in model.model_fields


@pytest.fixture
def sync_state(monkeypatch):
    """In-memory Mongo boundary; exercise the real lease runner, sync and marker handling."""
    from datetime import timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from registry.services import vector_sync_helpers as helpers
    from registry_pkgs.database.leased_job import Lease, LeaseLostError

    doc = ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName="latest",
        config={},
        vectorSyncPendingAt=datetime.now(UTC),
        vectorSyncAttempts=0,
    )
    state = SimpleNamespace(doc=doc, token=None, writes=[])

    async def get(*args, **kwargs):
        return state.doc.model_copy(deep=True) if state.doc is not None else None

    async def update(query, changes, **kwargs):
        if (
            state.doc is None
            or query.get("vectorSyncPendingAt", state.doc.vectorSyncPendingAt) != state.doc.vectorSyncPendingAt
        ):
            return SimpleNamespace(matched_count=0)
        for field, value in changes.get("$set", {}).items():
            setattr(state.doc, field, value)
        if "$unset" in changes:
            state.doc.vectorSyncPendingAt = None
            state.doc.vectorSyncAttempts = 0
        return SimpleNamespace(matched_count=1)

    collection = Mock(update_one=AsyncMock(side_effect=update))
    monkeypatch.setattr(ExtendedMCPServer, "get", AsyncMock(side_effect=get))
    monkeypatch.setattr(ExtendedMCPServer, "get_collection_name", lambda: "mcpservers")
    monkeypatch.setattr(ExtendedMCPServer, "get_pymongo_collection", lambda: collection)
    monkeypatch.setattr(helpers, "ensure_leader_document", AsyncMock())

    async def claim(**kwargs):
        if state.token is not None:
            return None
        state.token = uuid4().hex
        return Mock(), Lease(doc.id, "test", state.token, datetime.now(UTC) + timedelta(minutes=2))

    async def owned(lease, *args, **kwargs):
        if state.token != lease.token:
            raise LeaseLostError("taken over")

    async def release(repo, lease, **kwargs):
        if state.token == lease.token:
            state.token = None

    lease_repo = Mock(spec=helpers.LeasedRepository)
    lease_repo.spec = helpers.LEADER_LEASE
    lease_repo.claim = AsyncMock(side_effect=claim)
    lease_repo.assert_owned = AsyncMock(side_effect=owned)
    lease_repo.transition_or_raise = AsyncMock(side_effect=owned)
    monkeypatch.setattr(helpers, "_repository", lease_repo)
    monkeypatch.setattr(helpers, "release_leader", AsyncMock(side_effect=release))
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.start_transaction.return_value = AsyncMock()
    monkeypatch.setattr(helpers.MongoDB, "get_client", lambda: Mock(start_session=Mock(return_value=session)))
    state.session = session
    state.lease_repo = lease_repo
    state.collection = collection

    async def write(current, **kwargs):
        state.writes.append(current.serverName)
        return {"failed_tools": 0}

    state.repo = Mock(sync_to_vector_db=AsyncMock(side_effect=write))
    return state


async def test_obsolete_plan_reads_latest_and_clears_under_lease_transaction(sync_state):
    from datetime import timedelta

    from registry.services.vector_sync_helpers import sync_pending_vectors

    state = sync_state
    stale = state.doc.model_copy(deep=True)
    stale.serverName = "obsolete"
    plan = VectorSyncPlan(stale, state.doc.vectorSyncPendingAt - timedelta(seconds=1), False)
    await sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id, plan=plan)
    assert state.writes == ["latest"]
    assert state.doc.vectorSyncPendingAt is None
    assert state.lease_repo.transition_or_raise.await_args.kwargs["session"] is state.session
    assert state.collection.update_one.await_args.kwargs["session"] is state.session
    # Replaying a queued old plan after success must not recreate old vectors.
    assert await sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id, plan=plan) is False
    assert state.writes == ["latest"]


async def test_same_entity_contention_preserves_new_write(sync_state):
    import asyncio
    from datetime import timedelta

    from registry.services.vector_sync_helpers import VectorSyncBusyError, sync_pending_vectors

    state = sync_state
    started, finish = asyncio.Event(), asyncio.Event()

    async def slow(current, **kwargs):
        started.set()
        await finish.wait()
        state.writes.append(current.serverName)
        return {"failed_tools": 0}

    state.repo.sync_to_vector_db.side_effect = slow
    old = asyncio.create_task(sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id))
    await started.wait()
    state.doc.serverName = "newer"
    state.doc.vectorSyncPendingAt += timedelta(seconds=1)
    with pytest.raises(VectorSyncBusyError):
        await sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id)
    finish.set()
    await old
    assert state.doc.vectorSyncPendingAt is not None
    await sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id)
    assert state.writes == ["latest", "newer"]
    assert state.doc.vectorSyncPendingAt is None


@pytest.mark.parametrize("cancel", [False, True])
async def test_late_write_after_takeover_or_cancel_requeues_after_io(sync_state, cancel):
    import asyncio

    from registry.services.vector_sync_helpers import sync_pending_vectors
    from registry_pkgs.database.leased_job import LeaseLostError

    state = sync_state
    started, finish = asyncio.Event(), asyncio.Event()

    async def slow(current, **kwargs):
        started.set()
        await finish.wait()
        state.writes.append(current.serverName)
        return {"failed_tools": 0}

    state.repo.sync_to_vector_db.side_effect = slow
    task = asyncio.create_task(sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id))
    await started.wait()
    if cancel:
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()  # The thread-backed operation remains awaited, not detached.
    else:
        state.token = "new-owner"
    # Simulate a newer owner completing during the old operation.
    state.doc.vectorSyncPendingAt = None
    finish.set()
    with pytest.raises(asyncio.CancelledError if cancel else LeaseLostError):
        await task
    assert state.writes == ["latest"]
    assert state.doc.vectorSyncPendingAt is not None


@pytest.mark.parametrize("tools", [False, True])
@pytest.mark.parametrize(
    "result",
    [
        {"error": "write failed"},
        {"failed": 1, "metadata_updated": 1},
        {"metadata_updated": 0},
    ],
)
async def test_metadata_failure_or_missing_vectors_never_clears(sync_state, tools, result, monkeypatch):
    from registry.services.vector_sync_helpers import sync_pending_vectors
    from registry_pkgs.vector.sync_result import VectorSyncResult

    state = sync_state
    monkeypatch.setattr(ExtendedMCPServer, "to_documents", lambda self: [Mock()])
    state.repo.update_entity_metadata = AsyncMock(return_value=VectorSyncResult(**result))
    state.repo.update_tools_metadata = AsyncMock(return_value=VectorSyncResult(**result))
    marker = state.doc.vectorSyncPendingAt
    plan = VectorSyncPlan(state.doc, marker, False, ((("tool",), False),) if tools else ())
    with pytest.raises(RuntimeError):
        await sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id, plan=plan)
    assert state.doc.vectorSyncPendingAt == marker


async def test_federation_repair_marks_before_delete_and_keeps_marker_on_failure(sync_state):
    from registry.services.vector_sync_helpers import sync_pending_vectors

    state = sync_state
    state.doc.vectorSyncPendingAt = None

    async def fail_delete(*args):
        assert state.doc.vectorSyncPendingAt is not None
        assert state.token is not None
        raise RuntimeError("delete failed")

    state.repo.delete_by_runtime_identity = AsyncMock(side_effect=fail_delete)
    with pytest.raises(RuntimeError, match="delete failed"):
        await sync_pending_vectors(ExtendedMCPServer, state.repo, state.doc.id, runtime_identity=("fed", "arn"))
    assert state.doc.vectorSyncPendingAt is not None
    state.repo.sync_to_vector_db.assert_not_awaited()
