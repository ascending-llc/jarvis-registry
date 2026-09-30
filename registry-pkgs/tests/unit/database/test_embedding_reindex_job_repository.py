from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.database import embedding_reindex_job_repository as repository


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


@pytest.mark.asyncio
async def test_transition_applies_write_when_owner_holds_running_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)

    ok = await repository.transition_embedding_reindex_job(
        job_id=PydanticObjectId(), lease_owner="worker-1", set_fields={"status": "completed"}
    )

    assert ok is True
    flt, update = collection.update_one.await_args.args[0], collection.update_one.await_args.args[1]
    # The lease gate: only a RUNNING job owned by this pod is writable.
    assert flt["status"] == "running"
    assert flt["leaseOwner"] == "worker-1"
    assert update["$set"]["status"] == "completed"
    assert "updatedAt" in update["$set"]


@pytest.mark.asyncio
async def test_transition_returns_false_when_not_owner_or_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    # modified_count == 0: the filter matched nothing (lease taken over, or the job is already
    # COMPLETED/FAILED so status != RUNNING).
    _patch_update(monkeypatch, 0)

    ok = await repository.transition_embedding_reindex_job(
        job_id=PydanticObjectId(), lease_owner="worker-1", set_fields={"status": "failed"}
    )

    assert ok is False


@pytest.mark.asyncio
async def test_transition_forwards_session(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = _patch_update(monkeypatch, 1)
    sentinel = object()

    await repository.transition_embedding_reindex_job(
        job_id=PydanticObjectId(), lease_owner="w", set_fields={"switchedAt": 1}, session=sentinel
    )

    assert collection.update_one.await_args.kwargs["session"] is sentinel
