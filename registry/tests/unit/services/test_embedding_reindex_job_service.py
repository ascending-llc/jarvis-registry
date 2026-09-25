from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import embedding_reindex_job_service as svc_module
from registry.services.embedding_reindex_job_service import (
    EmbeddingModelSmokeTestError,
    EmbeddingReindexAlreadyRunningError,
    EmbeddingReindexJobService,
)
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus

pytestmark = pytest.mark.asyncio


def _service(selection_service=None):
    settings = SimpleNamespace(vector_config=SimpleNamespace(), encryption_key=b"key")
    return EmbeddingReindexJobService(
        settings=settings,
        selection_service=selection_service or MagicMock(),
    )


# --- claim / heartbeat concurrency boundary ---


async def test_claim_job_uses_atomic_lease_update(monkeypatch):
    expected = SimpleNamespace(id=PydanticObjectId())
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value={"_id": expected.id})
    monkeypatch.setattr(EmbeddingReindexJob, "get_pymongo_collection", lambda: collection)
    monkeypatch.setattr(EmbeddingReindexJob, "model_validate", lambda document: expected)

    result = await _service().claim_job(lease_owner="worker-1", lease_duration=timedelta(minutes=5))

    assert result is expected
    query, update = collection.find_one_and_update.await_args.args
    # Only a RUNNING job that is unclaimed OR whose lease has expired is claimable (crash recovery).
    assert query["status"] == EmbeddingReindexJobStatus.RUNNING.value
    assert {"leaseOwner": None} in query["$or"]
    assert any("leaseExpiresAt" in clause for clause in query["$or"])
    assert update["$set"]["leaseOwner"] == "worker-1"


async def test_claim_job_returns_none_when_nothing_claimable(monkeypatch):
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value=None)
    monkeypatch.setattr(EmbeddingReindexJob, "get_pymongo_collection", lambda: collection)

    result = await _service().claim_job(lease_owner="worker-1", lease_duration=timedelta(minutes=5))

    assert result is None


async def test_heartbeat_only_renews_owned_running_job(monkeypatch):
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    monkeypatch.setattr(EmbeddingReindexJob, "get_pymongo_collection", lambda: collection)

    renewed = await _service().heartbeat(
        job_id=PydanticObjectId(), lease_owner="worker-1", lease_duration=timedelta(minutes=5)
    )

    assert renewed is True
    query, _update = collection.update_one.await_args.args
    assert query["status"] == EmbeddingReindexJobStatus.RUNNING.value
    assert query["leaseOwner"] == "worker-1"


async def test_heartbeat_returns_false_when_lease_lost(monkeypatch):
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=0))
    monkeypatch.setattr(EmbeddingReindexJob, "get_pymongo_collection", lambda: collection)

    renewed = await _service().heartbeat(
        job_id=PydanticObjectId(), lease_owner="worker-1", lease_duration=timedelta(minutes=5)
    )

    assert renewed is False


# --- trigger orchestration ---


async def test_trigger_reindex_captures_previous_pair_and_returns_current_selection(monkeypatch):
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    service = _service(selection)
    service.get_active_job = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", AsyncMock())
    # The live selection the job records as its previous pair, and the 202 body returns unchanged.
    prev_model = PydanticObjectId()
    current = SimpleNamespace(embeddingModelSourceId=prev_model, embeddingCollectionGeneration="gen0")
    monkeypatch.setattr(svc_module, "get_model_gateway_selection", AsyncMock(return_value=current))
    created = SimpleNamespace(id=PydanticObjectId(), insert=AsyncMock())
    fake_job_cls = MagicMock(return_value=created)
    monkeypatch.setattr(svc_module, "EmbeddingReindexJob", fake_job_cls)

    result = await service.trigger_reindex("abc", updated_by="user-1")

    created.insert.assert_awaited_once()
    kwargs = fake_job_cls.call_args.kwargs
    assert kwargs["targetEmbeddingModelSourceId"] == source.id
    assert kwargs["requestedBy"] == "user-1"
    # The previous (model, generation) pair is captured for the compare-and-set commit.
    assert kwargs["previousEmbeddingModelSourceId"] == prev_model
    assert kwargs["previousCollectionGeneration"] == "gen0"
    # 202 body = the CURRENT selection, unchanged (switches only on completion).
    assert result is current


async def test_trigger_reindex_409_when_already_running(monkeypatch):
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    service = _service(selection)
    service.get_active_job = AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId()))
    smoke = AsyncMock()
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", smoke)
    insert = AsyncMock()
    monkeypatch.setattr(EmbeddingReindexJob, "insert", insert)

    with pytest.raises(EmbeddingReindexAlreadyRunningError):
        await service.trigger_reindex("abc", updated_by="user-1")

    # Rejected before smoke test / job creation.
    smoke.assert_not_awaited()
    insert.assert_not_awaited()


async def test_trigger_reindex_502_on_smoke_failure_persists_nothing(monkeypatch):
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    service = _service(selection)
    service.get_active_job = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", AsyncMock(side_effect=RuntimeError("bad creds")))
    insert = AsyncMock()
    monkeypatch.setattr(EmbeddingReindexJob, "insert", insert)

    with pytest.raises(EmbeddingModelSmokeTestError, match="bad creds"):
        await service.trigger_reindex("abc", updated_by="user-1")

    insert.assert_not_awaited()
