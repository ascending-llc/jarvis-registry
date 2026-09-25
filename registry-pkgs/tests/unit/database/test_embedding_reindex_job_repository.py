from datetime import UTC, datetime, timedelta
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
