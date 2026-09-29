from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import embedding_reindex_job_service as svc_module
from registry.services.embedding_reindex_job_service import (
    EmbeddingModelSmokeTestError,
    EmbeddingReindexAlreadyRunningError,
    EmbeddingReindexFederationSyncActiveError,
    EmbeddingReindexJobService,
)
from registry_pkgs.models.embedding_reindex_job import EmbeddingReindexJob
from registry_pkgs.models.enums import EmbeddingReindexJobStatus

pytestmark = pytest.mark.asyncio


def _service(selection_service=None, federation_job_service=None):
    settings = SimpleNamespace(vector_config=SimpleNamespace(), encryption_key=b"key")
    return EmbeddingReindexJobService(
        settings=settings,
        selection_service=selection_service or MagicMock(),
        federation_job_service=federation_job_service or SimpleNamespace(list_active_jobs=AsyncMock(return_value=[])),
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
    # Every claim counts as an attempt, in the same atomic update (drives the retry cap).
    assert update["$inc"] == {"attempts": 1}
    # NOT filtered on attempts: a used-up job must stay claimable to be finalized FAILED.
    assert "attempts" not in query


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


async def test_trigger_reindex_409_when_federation_sync_active(monkeypatch):
    source = SimpleNamespace(id=PydanticObjectId())
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=source)
    job_id, federation_id = PydanticObjectId(), PydanticObjectId()
    active = SimpleNamespace(
        id=job_id,
        federationId=federation_id,
        status="syncing",
        createdAt=datetime(2026, 9, 1, tzinfo=UTC),
    )
    federation = SimpleNamespace(list_active_jobs=AsyncMock(return_value=[active]))
    service = _service(selection, federation)
    service.get_active_job = AsyncMock(return_value=None)
    smoke = AsyncMock()
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", smoke)
    get_selection = AsyncMock()
    monkeypatch.setattr(svc_module, "get_model_gateway_selection", get_selection)
    fake_job_cls = MagicMock()
    monkeypatch.setattr(svc_module, "EmbeddingReindexJob", fake_job_cls)

    with pytest.raises(EmbeddingReindexFederationSyncActiveError) as exc_info:
        await service.trigger_reindex("abc", updated_by="user-1")

    # The detail names the job so an operator can tell a live sync from an orphan.
    message = str(exc_info.value)
    assert str(job_id) in message
    assert str(federation_id) in message
    assert "syncing" in message
    assert "2026-09-01" in message
    # Bounded lookup across all federations.
    assert federation.list_active_jobs.await_args.kwargs == {"limit": svc_module._FEDERATION_JOBS_REPORTED}
    # Rejected before the smoke test, with no job created and the selection untouched.
    smoke.assert_not_awaited()
    get_selection.assert_not_awaited()
    fake_job_cls.assert_not_called()


async def test_trigger_reindex_names_every_reported_federation_job(monkeypatch):
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId()))
    jobs = [
        SimpleNamespace(
            id=PydanticObjectId(),
            federationId=PydanticObjectId(),
            status=status,
            createdAt=datetime(2026, 9, day, tzinfo=UTC),
        )
        for day, status in ((1, "pending"), (2, "syncing"))
    ]
    service = _service(selection, SimpleNamespace(list_active_jobs=AsyncMock(return_value=jobs)))
    service.get_active_job = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", AsyncMock())

    with pytest.raises(EmbeddingReindexFederationSyncActiveError) as exc_info:
        await service.trigger_reindex("abc", updated_by="user-1")

    message = str(exc_info.value)
    assert all(str(job.id) in message and str(job.federationId) in message for job in jobs)


async def test_trigger_reindex_already_running_wins_over_federation_check(monkeypatch):
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId()))
    federation = SimpleNamespace(list_active_jobs=AsyncMock(return_value=[]))
    service = _service(selection, federation)
    service.get_active_job = AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId()))

    with pytest.raises(EmbeddingReindexAlreadyRunningError):
        await service.trigger_reindex("abc", updated_by="user-1")

    federation.list_active_jobs.assert_not_awaited()


async def test_trigger_reindex_proceeds_when_no_federation_sync_active(monkeypatch):
    selection = MagicMock()
    selection.resolve_embedding_model_source = AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId()))
    federation = SimpleNamespace(list_active_jobs=AsyncMock(return_value=[]))
    service = _service(selection, federation)
    service.get_active_job = AsyncMock(return_value=None)
    smoke = AsyncMock()
    monkeypatch.setattr(svc_module, "smoke_test_embedding_model", smoke)
    current = SimpleNamespace(embeddingModelSourceId=None, embeddingCollectionGeneration=None)
    monkeypatch.setattr(svc_module, "get_model_gateway_selection", AsyncMock(return_value=current))
    created = SimpleNamespace(insert=AsyncMock())
    monkeypatch.setattr(svc_module, "EmbeddingReindexJob", MagicMock(return_value=created))

    result = await service.trigger_reindex("abc", updated_by="user-1")

    federation.list_active_jobs.assert_awaited_once()
    smoke.assert_awaited_once()
    created.insert.assert_awaited_once()
    assert result is current
