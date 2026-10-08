from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.database import embedding_reindex_job_repository as repository
from registry_pkgs.database.leased_job import Lease


def _patch_collection(monkeypatch: pytest.MonkeyPatch, return_value) -> AsyncMock:
    collection = AsyncMock()
    collection.find_one.return_value = return_value
    monkeypatch.setattr(
        repository.EmbeddingReindexJob,
        "get_pymongo_collection",
        classmethod(lambda cls: collection),
    )
    return collection


@pytest.mark.asyncio
async def test_active_job_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = datetime.now(UTC) + timedelta(minutes=2)
    document = {
        "_id": PydanticObjectId(),
        "targetEmbeddingModelSourceId": PydanticObjectId(),
        "status": "running",
        "leaseExpiresAt": lease,
        "startedAt": datetime.now(UTC),
        "createdAt": datetime.now(UTC),
        "updatedAt": datetime.now(UTC),
    }
    _patch_collection(monkeypatch, document)

    result = await repository.get_active_embedding_reindex_job()

    assert result is not None
    assert result.status == "running"


@pytest.mark.asyncio
async def test_query_filters_running_and_unexpired_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_collection(monkeypatch, None)
    before = datetime.now(UTC)

    result = await repository.get_active_embedding_reindex_job()

    assert result is None
    query = collection.find_one.await_args.args[0]
    # Only RUNNING jobs whose lease is still in the future count as active; an
    # expired lease (crashed reindex) is excluded by the $gt filter.
    assert query["status"] == "running"
    assert set(query["leaseExpiresAt"].keys()) == {"$gt"}
    assert query["leaseExpiresAt"]["$gt"] >= before


@pytest.mark.asyncio
async def test_no_matching_job_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_collection(monkeypatch, None)

    assert await repository.get_active_embedding_reindex_job() is None


def _patch_update(monkeypatch: pytest.MonkeyPatch, modified_count: int) -> AsyncMock:
    collection = AsyncMock()
    collection.update_one.return_value = SimpleNamespace(modified_count=modified_count)
    monkeypatch.setattr(repository.EmbeddingReindexJob, "get_pymongo_collection", classmethod(lambda cls: collection))
    return collection


def _lease(doc_id: PydanticObjectId | None = None) -> Lease:
    return Lease(doc_id=doc_id or PydanticObjectId(), owner="worker-1", token="tok-1", expires_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_transition_applies_write_when_token_holds_running_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    lease = _lease()

    ok = await repository.embedding_reindex_repository.transition(lease, {"status": "completed"})

    assert ok is True
    flt, update = collection.update_one.await_args.args[0], collection.update_one.await_args.args[1]
    # The lease gate fences on the per-claim token and the RUNNING status.
    assert flt["_id"] == lease.doc_id
    assert flt["leaseToken"] == lease.token
    assert flt["status"] == {"$in": ["running"]}
    assert update["$set"]["status"] == "completed"
    assert "updatedAt" in update["$set"]


@pytest.mark.asyncio
async def test_transition_returns_false_when_token_stale_or_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    # modified_count == 0: the fence matched nothing (lease re-claimed with a new token, or the job is
    # already COMPLETED/FAILED so status != RUNNING).
    _patch_update(monkeypatch, 0)

    ok = await repository.embedding_reindex_repository.transition(_lease(), {"status": "failed"})

    assert ok is False


@pytest.mark.asyncio
async def test_transition_release_clears_lease_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)

    await repository.embedding_reindex_repository.transition(_lease(), {"status": "completed"}, release=True)

    sets = collection.update_one.await_args.args[1]["$set"]
    assert sets["leaseOwner"] is None
    assert sets["leaseToken"] is None
    assert sets["leaseExpiresAt"] is None


@pytest.mark.asyncio
async def test_transition_forwards_session(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    sentinel = object()

    await repository.embedding_reindex_repository.transition(_lease(), {"switchedAt": 1}, session=sentinel)

    assert collection.update_one.await_args.kwargs["session"] is sentinel
