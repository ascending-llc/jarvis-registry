from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

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


async def test_trigger_reindex_defers_selection_and_creates_job(monkeypatch):
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    selection.set_embedding_model = AsyncMock()
    selection.get_selection_or_none = AsyncMock(return_value=SimpleNamespace(defaultWorkflowModelSourceId=None))
    service = _service(selection)
    service.get_active_job = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", AsyncMock())
    created = SimpleNamespace(id=PydanticObjectId(), insert=AsyncMock())
    fake_job_cls = MagicMock(return_value=created)
    monkeypatch.setattr(svc_module, "EmbeddingReindexJob", fake_job_cls)

    result = await service.trigger_reindex("abc", updated_by="user-1")

    created.insert.assert_awaited_once()
    # Selection is NOT persisted at trigger time — the executor commits it after a successful swap.
    selection.set_embedding_model.assert_not_awaited()
    assert fake_job_cls.call_args.kwargs["targetEmbeddingModelSourceId"] == source.id
    assert fake_job_cls.call_args.kwargs["triggeredBy"] == "user-1"
    # The 202 response reflects the requested target without having persisted it.
    assert result.embeddingModelSourceId == source.id
    assert result.defaultWorkflowModelSourceId is None


async def test_trigger_reindex_409_when_already_running(monkeypatch):
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    selection.set_embedding_model = AsyncMock()
    service = _service(selection)
    service.get_active_job = AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId()))
    smoke = AsyncMock()
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", smoke)
    insert = AsyncMock()
    monkeypatch.setattr(EmbeddingReindexJob, "insert", insert)

    with pytest.raises(EmbeddingReindexAlreadyRunningError):
        await service.trigger_reindex("abc", updated_by="user-1")

    # Nothing persisted: no smoke test, no selection write, no job.
    smoke.assert_not_awaited()
    selection.set_embedding_model.assert_not_awaited()
    insert.assert_not_awaited()


async def test_trigger_reindex_502_on_smoke_failure_persists_nothing(monkeypatch):
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    selection.set_embedding_model = AsyncMock()
    service = _service(selection)
    service.get_active_job = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", AsyncMock(side_effect=RuntimeError("bad creds")))
    insert = AsyncMock()
    monkeypatch.setattr(EmbeddingReindexJob, "insert", insert)

    with pytest.raises(EmbeddingModelSmokeTestError, match="bad creds"):
        await service.trigger_reindex("abc", updated_by="user-1")

    selection.set_embedding_model.assert_not_awaited()
    insert.assert_not_awaited()


async def test_trigger_reindex_maps_duplicate_key_to_409(monkeypatch):
    # Two truly simultaneous triggers both pass get_active_job(); the partial unique index rejects
    # the loser's insert with a duplicate-key error, which must surface as "already running".
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    service = _service(selection)
    service.get_active_job = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", AsyncMock())
    created = SimpleNamespace(insert=AsyncMock(side_effect=DuplicateKeyError("dup")))
    monkeypatch.setattr(svc_module, "EmbeddingReindexJob", MagicMock(return_value=created))

    with pytest.raises(EmbeddingReindexAlreadyRunningError):
        await service.trigger_reindex("abc", updated_by="user-1")
