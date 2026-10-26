"""Lease lifecycle regressions using real services and an isolated Mongo collection fake."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services.background_task_tracker import BackgroundTaskTracker
from registry.services.federation_crud_service import FederationCrudService
from registry.services.federation_job_service import FederationJobService, federation_sync_lease_repository
from registry.services.federation_sync_service import (
    _INTERRUPTED_REASON,
    FederationSyncMutationResult,
    FederationSyncShuttingDownError,
    VectorSyncOutcome,
)
from registry_pkgs.database.leased_job import LeaseLostError
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobStatus,
    FederationJobType,
    FederationProviderType,
    FederationStatus,
    FederationSyncStatus,
    FederationTriggerType,
)
from registry_pkgs.models.federation import Federation, FederationLastSync, FederationStats
from registry_pkgs.models.federation_sync_job import FederationApplySummary, FederationSyncJob
from tests.unit.services.federation_sync_test_helpers import _make_federation_sync_service, _patch_mongo_session

pytestmark = pytest.mark.asyncio
_MISSING = object()


def _matches(document: dict, query: dict) -> bool:
    """Evaluate the small subset of Mongo predicates used by these lifecycle operations."""
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        value = document
        for part in key.split("."):
            value = value.get(part, _MISSING) if isinstance(value, dict) else _MISSING
        if not isinstance(condition, dict):
            if (None if value is _MISSING else value) != condition:
                return False
            continue
        for operator, operand in condition.items():
            if operator == "$exists":
                matched = (value is not _MISSING) == operand
            elif operator == "$in":
                matched = value in operand
            elif operator == "$gt":
                matched = value is not _MISSING and value is not None and value > operand
            elif operator == "$lte":
                matched = value is not _MISSING and value is not None and value <= operand
            else:
                raise AssertionError(f"Unsupported test query operator {operator}")
            if not matched:
                return False
    return True


class _Cursor:
    def __init__(self, documents: list, model=None) -> None:
        self.documents = documents
        self.model = model

    def sort(self, fields):
        for field, direction in reversed(fields):
            self.documents.sort(key=lambda doc: doc[field], reverse=direction == -1)
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    async def to_list(self, length=None):
        documents = deepcopy(self.documents[:length])
        return [self.model.model_validate(doc) for doc in documents] if self.model else documents


class _Collection:
    def __init__(self) -> None:
        self.documents: dict = {}
        self.writes: list = []
        self.aggregate_calls: list = []
        self.distinct_calls: list = []

    def find(self, query, *, projection=None, model=None):
        documents = [doc for doc in self.documents.values() if _matches(doc, query)]
        if projection is not None:
            documents = [{key: doc[key] for key in projection if key in doc} for doc in documents]
        return _Cursor(documents, model)

    async def distinct(self, field, query):
        self.distinct_calls.append(deepcopy(query))
        return list({doc[field] for doc in self.documents.values() if _matches(doc, query)})

    async def aggregate(self, pipeline):
        self.aggregate_calls.append(deepcopy(pipeline))
        federation_ids = pipeline[0]["$match"]["federationId"]["$in"]
        grouped = []
        for federation_id in federation_ids:
            jobs = [doc for doc in self.documents.values() if doc["federationId"] == federation_id]
            if jobs:
                grouped.append({"_id": federation_id, "job": max(jobs, key=lambda doc: doc["createdAt"])})
        return _Cursor(grouped)

    async def find_one(self, query, sort=None, session=None):
        cursor = self.find(query)
        if sort:
            cursor.sort(sort)
        documents = await cursor.limit(1).to_list()
        return documents[0] if documents else None

    async def update_one(self, query, update, *, session=None):
        self.writes.append((deepcopy(query), deepcopy(update), session))
        for doc in self.documents.values():
            if _matches(doc, query):
                doc.update(deepcopy(update["$set"]))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def find_one_and_update(self, query, update, **kwargs):
        doc = await self.find_one(query)
        if doc is None:
            return None
        await self.update_one({"_id": doc["_id"]}, update)
        return deepcopy(self.documents[doc["_id"]])


@pytest.fixture
def storage(monkeypatch):
    jobs, federations = _Collection(), _Collection()
    monkeypatch.setattr(FederationSyncJob, "get_pymongo_collection", lambda *args: jobs)
    monkeypatch.setattr(Federation, "get_pymongo_collection", lambda *args: federations)

    async def insert(job, session=None):
        jobs.documents[job.id] = job.model_dump(by_alias=True)
        return job

    async def find_job(query, **kwargs):
        doc = await jobs.find_one(query, **kwargs)
        return FederationSyncJob.model_validate(doc) if doc else None

    async def get_federation(doc_id, **kwargs):
        doc = await federations.find_one({"_id": doc_id})
        return Federation.model_validate(doc) if doc else None

    monkeypatch.setattr(FederationSyncJob, "insert", insert)
    monkeypatch.setattr(FederationSyncJob, "find_one", find_job)
    monkeypatch.setattr(FederationSyncJob, "find", lambda query: jobs.find(query, model=FederationSyncJob))
    monkeypatch.setattr(Federation, "get", get_federation)
    service = _make_federation_sync_service()
    service.federation_crud_service = FederationCrudService()
    service.federation_job_service = FederationJobService()
    service._background_task_tracker = BackgroundTaskTracker()
    service._lease_owner = "registry-test-pod"
    federation = Federation(
        id=PydanticObjectId(),
        providerType=FederationProviderType.AWS_AGENTCORE,
        displayName="Original",
        providerConfig={"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/Test"},
        status=FederationStatus.ACTIVE,
        syncStatus=FederationSyncStatus.IDLE,
    )
    federations.documents[federation.id] = federation.model_dump(by_alias=True)
    _patch_mongo_session(monkeypatch)
    return SimpleNamespace(service=service, jobs=jobs, federations=federations, federation=federation)


async def _pending(storage, *, job_type=FederationJobType.FULL_SYNC):
    return await storage.service.create_sync_job_and_mark_pending(
        federation=storage.federation,
        job_type=job_type,
        trigger_type=FederationTriggerType.MANUAL,
        triggered_by="user-1",
        request_snapshot={},
        session=None,
    )


@pytest.mark.parametrize("entry", ["background", "manual", "resync", "delete"])
async def test_every_creation_path_inserts_leased_job(storage, entry):
    service = storage.service
    executed = []

    async def execution(*, job, lease, **kwargs):
        inserted = storage.jobs.documents[job.id]
        assert inserted["status"] == FederationJobStatus.PENDING
        assert inserted["leaseToken"] == lease.token
        assert inserted["leaseOwner"] == service._lease_owner
        assert inserted["leaseExpiresAt"] > datetime.now(UTC)
        assert lease.doc_id == job.id
        executed.append(job)
        return job

    service.run_sync = AsyncMock(side_effect=execution)
    service.run_delete = AsyncMock(side_effect=execution)
    if entry == "background":
        await service.start_manual_sync_background(federation=storage.federation, reason=None, triggered_by="user-1")
        tasks = list(service._background_task_tracker._tasks)
        await asyncio.gather(*tasks)
    elif entry == "manual":
        await service.start_manual_sync(federation=storage.federation, reason=None, triggered_by="user-1")
    elif entry == "resync":
        await service.update_federation_with_optional_resync(
            federation=storage.federation,
            display_name="Updated",
            description=None,
            tags=[],
            provider_config={**storage.federation.providerConfig, "region": "us-west-2"},
            updated_by="user-1",
            sync_after_update=True,
        )
    else:
        await service.start_delete(federation=storage.federation, triggered_by="user-1")
    assert len(executed) == 1


async def test_job_save_is_forbidden(storage):
    job, _ = await _pending(storage)
    with pytest.raises(TypeError):
        await job.save()


@pytest.mark.parametrize("mutator", ["syncing", "discovery", "apply", "success", "failed"])
async def test_every_job_write_is_token_fenced(storage, mutator):
    job, lease = await _pending(storage)
    storage.jobs.documents[job.id]["leaseToken"] = "replacement-token"
    service = storage.service.federation_job_service
    with pytest.raises(LeaseLostError):
        match mutator:
            case "syncing":
                await service.mark_syncing(job, FederationJobPhase.DISCOVERING, lease)
            case "discovery":
                await service.update_discovery_summary(job, 1, 2, lease)
            case "apply":
                await service.update_apply_summary(job, FederationApplySummary(errors=1), lease)
            case "success":
                job.status = FederationJobStatus.SYNCING
                await service.mark_success(job, lease)
            case "failed":
                await service.mark_failed(job, FederationJobPhase.FAILED, "error", lease)
    assert storage.jobs.documents[job.id]["status"] == FederationJobStatus.PENDING


async def test_expired_lease_cannot_write_with_same_token(storage):
    job, lease = await _pending(storage)
    storage.jobs.documents[job.id]["leaseExpiresAt"] = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(LeaseLostError):
        await storage.service.federation_job_service.mark_syncing(job, FederationJobPhase.DISCOVERING, lease)

    assert storage.jobs.documents[job.id]["status"] == FederationJobStatus.PENDING


@pytest.mark.parametrize("at_finalize", [False, True])
async def test_lost_job_fence_never_writes_federation(storage, at_finalize):
    service = storage.service
    job, lease = await _pending(storage)
    storage.jobs.documents[job.id]["leaseToken"] = "replacement-token"
    storage.federations.writes.clear()
    service._discover_entities = AsyncMock(return_value={})
    service._build_federation_stats = AsyncMock(return_value=FederationStats())
    coro = (
        service._finalize_sync_status(
            storage.federation,
            job,
            FederationSyncMutationResult(FederationApplySummary()),
            VectorSyncOutcome(),
            lease,
        )
        if at_finalize
        else service.run_sync(storage.federation, job, PydanticObjectId(), lease)
    )
    with pytest.raises(LeaseLostError):
        await service.execute_leased_federation_job(
            lease=lease,
            job=job,
            coro=coro,
            interrupted_reason=_INTERRUPTED_REASON,
        )
    assert not storage.federations.writes


@pytest.mark.parametrize("sync_failed", [False, True])
async def test_terminal_parent_write_lands_before_lease_release(storage, sync_failed):
    service = storage.service
    job, lease = await _pending(storage)
    await service.federation_job_service.mark_syncing(job, FederationJobPhase.SYNCING_VECTORS, lease)
    if sync_failed:
        job.discoverySummary.discoveredMcpServers = 1
    service._build_federation_stats = AsyncMock(return_value=FederationStats())
    crud = service.federation_crud_service
    method_name = "mark_sync_failed" if sync_failed else "mark_sync_success"
    parent_write = getattr(crud, method_name)
    job_at_parent_write = {}

    async def snapshot_then_write(*args, **kwargs):
        job_at_parent_write.update(deepcopy(storage.jobs.documents[job.id]))
        return await parent_write(*args, **kwargs)

    setattr(crud, method_name, snapshot_then_write)
    await service._finalize_sync_status(
        storage.federation,
        job,
        FederationSyncMutationResult(FederationApplySummary()),
        VectorSyncOutcome(),
        lease,
    )

    # The heartbeat runs until the execution returns, so the lease must still be held while the
    # parent is written; releasing it first lets a renewal raise LeaseLostError mid-write.
    assert job_at_parent_write["leaseToken"] == lease.token
    assert job_at_parent_write["status"] == FederationJobStatus.SYNCING
    expected_job = FederationJobStatus.FAILED if sync_failed else FederationJobStatus.SUCCESS
    expected_parent = FederationSyncStatus.FAILED if sync_failed else FederationSyncStatus.SUCCESS
    assert storage.jobs.documents[job.id]["status"] == expected_job
    assert storage.jobs.documents[job.id]["leaseToken"] is None
    assert storage.federations.documents[storage.federation.id]["syncStatus"] == expected_parent


async def test_late_terminal_parent_write_cannot_overwrite_reaper_repair(storage):
    job, _ = await _pending(storage)
    stale = storage.federation.model_copy(deep=True)
    fed = storage.federations.documents[storage.federation.id]
    fed["syncStatus"] = FederationSyncStatus.FAILED
    fed["syncMessage"] = "Executor lost: lease expired"

    await storage.service.federation_crud_service.mark_sync_success(
        stale,
        FederationLastSync(jobId=job.id, status=FederationSyncStatus.SUCCESS),
        FederationStats(),
        job_id=job.id,
    )

    assert fed["syncStatus"] == FederationSyncStatus.FAILED
    assert fed["syncMessage"] == "Executor lost: lease expired"


async def test_reaper_leaves_parent_alone_when_executor_already_finalized_it(storage):
    job, _ = await _pending(storage)
    stored = storage.jobs.documents[job.id]
    stored["status"] = FederationJobStatus.SYNCING
    stored["leaseExpiresAt"] = datetime.now(UTC) - timedelta(seconds=1)
    fed = storage.federations.documents[storage.federation.id]
    fed["syncStatus"] = FederationSyncStatus.SUCCESS

    await storage.service.reap_expired_federation_jobs()

    assert stored["status"] == FederationJobStatus.FAILED
    assert fed["syncStatus"] == FederationSyncStatus.SUCCESS


async def test_reaper_continues_past_a_failing_parent_repair(storage, monkeypatch):
    service = storage.service
    job, _ = await _pending(storage)
    stored = storage.jobs.documents[job.id]
    stored["status"] = FederationJobStatus.SYNCING
    stored["leaseExpiresAt"] = datetime.now(UTC) - timedelta(seconds=1)
    storage.federations.documents[storage.federation.id]["syncStatus"] = FederationSyncStatus.SYNCING
    stuck = Federation(
        id=PydanticObjectId(),
        providerType=FederationProviderType.AWS_AGENTCORE,
        displayName="Stuck",
        providerConfig={"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/Test"},
        status=FederationStatus.ACTIVE,
        syncStatus=FederationSyncStatus.SYNCING,
        updatedAt=datetime.now(UTC) - timedelta(minutes=3),
    )
    storage.federations.documents[stuck.id] = stuck.model_dump(by_alias=True)
    finalize = service._finalize_abandoned_job

    async def flaky_finalize(federation_id, *args, **kwargs):
        if federation_id == storage.federation.id:
            raise RuntimeError("mongo blip")
        return await finalize(federation_id, *args, **kwargs)

    monkeypatch.setattr(service, "_finalize_abandoned_job", flaky_finalize)
    await service.reap_expired_federation_jobs()

    assert stored["status"] == FederationJobStatus.FAILED
    # Pass 3 still ran after pass 1's repair raised.
    assert storage.federations.documents[stuck.id]["syncStatus"] == FederationSyncStatus.FAILED


@pytest.mark.parametrize("repairable", [True, False])
async def test_stuck_pass_counts_only_federations_it_repaired(storage, repairable):
    job, _ = await _pending(storage)
    stored = storage.jobs.documents[job.id]
    stored["status"] = FederationJobStatus.FAILED
    stored["updatedAt"] = datetime.now(UTC) - timedelta(minutes=3)
    fed = storage.federations.documents[storage.federation.id]
    fed["syncStatus"] = FederationSyncStatus.SYNCING
    fed["updatedAt"] = datetime.now(UTC) - timedelta(minutes=3)
    if not repairable:
        # The parent names another job, so the lastSync.jobId guard rejects every repair.
        fed["lastSync"]["jobId"] = PydanticObjectId()

    reaped = await storage.service.reap_expired_federation_jobs()

    assert reaped == (1 if repairable else 0)
    expected = FederationSyncStatus.FAILED if repairable else FederationSyncStatus.SYNCING
    assert fed["syncStatus"] == expected


async def test_lost_lease_during_discovery_never_writes_federation(storage):
    service = storage.service
    job, lease = await _pending(storage)
    storage.jobs.documents[job.id]["leaseToken"] = "replacement-token"
    storage.federations.writes.clear()
    service._discover_entities = AsyncMock(side_effect=RuntimeError("provider unavailable"))

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.execute_leased_federation_job(
            lease=lease,
            job=job,
            coro=service.run_sync(storage.federation, job, PydanticObjectId(), lease),
            interrupted_reason=_INTERRUPTED_REASON,
        )

    assert not storage.federations.writes


@pytest.mark.parametrize("fallback_fails", [False, True])
async def test_failure_cleanup_preserves_original_exception(storage, monkeypatch, fallback_fails):
    service = storage.service
    job, lease = await _pending(storage)
    original = RuntimeError("discovery failed")
    service._discover_entities = AsyncMock(side_effect=original)
    service.federation_crud_service.mark_sync_failed = AsyncMock(side_effect=RuntimeError("parent unavailable"))
    if fallback_fails:
        monkeypatch.setattr(
            federation_sync_lease_repository, "transition", AsyncMock(side_effect=RuntimeError("db down"))
        )
    with pytest.raises(RuntimeError) as exc:
        await service.execute_leased_federation_job(
            lease=lease,
            job=job,
            coro=service.run_sync(storage.federation, job, PydanticObjectId(), lease),
            interrupted_reason=_INTERRUPTED_REASON,
        )
    assert exc.value is original
    if not fallback_fails:
        stored = storage.jobs.documents[job.id]
        assert stored["status"] == FederationJobStatus.FAILED
        assert stored["leaseToken"] is None


async def test_tracker_rejects_spawn_and_finalizes_both_records(storage):
    service = storage.service
    await service._background_task_tracker.shutdown()
    with pytest.raises(FederationSyncShuttingDownError):
        await service.start_manual_sync_background(federation=storage.federation, reason=None, triggered_by="user-1")
    stored = next(iter(storage.jobs.documents.values()))
    assert stored["status"] == FederationJobStatus.FAILED
    assert stored["leaseToken"] is None
    assert stored["error"] == _INTERRUPTED_REASON
    assert storage.federations.documents[storage.federation.id]["syncStatus"] == FederationSyncStatus.FAILED


async def test_tracker_cancellation_repairs_parent_after_execution_stops(storage):
    service = storage.service
    started = asyncio.Event()
    stopped = asyncio.Event()
    finalizer = service._finalize_abandoned_job

    async def execute(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def finalize(*args, **kwargs):
        assert stopped.is_set()
        await finalizer(*args, **kwargs)

    service.run_sync = AsyncMock(side_effect=execute)
    service._finalize_abandoned_job = finalize
    job = await service.start_manual_sync_background(federation=storage.federation, reason=None, triggered_by="user-1")
    await asyncio.wait_for(started.wait(), timeout=1)
    assert await service._background_task_tracker.shutdown() == 0
    stored = storage.jobs.documents[job.id]
    assert stored["status"] == FederationJobStatus.FAILED
    assert stored["leaseToken"] is None
    assert stored["error"] == _INTERRUPTED_REASON
    assert storage.federations.documents[storage.federation.id]["syncStatus"] == FederationSyncStatus.FAILED


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "delete",
        "missing",
        "newer",
        "legacy_old",
        "legacy_recent",
        "stuck",
        "stuck_delete",
        "no_job",
        "live",
        "recent_parent",
        "recent_terminal_job",
    ],
)
async def test_reaper_three_passes(storage, case, caplog):
    job, _ = await _pending(storage)
    stored = storage.jobs.documents[job.id]
    fed = storage.federations.documents[storage.federation.id]
    now = datetime.now(UTC)
    stored["status"] = FederationJobStatus.SYNCING
    fed["syncStatus"] = FederationSyncStatus.SYNCING
    fed["updatedAt"] = now - timedelta(minutes=3)
    if case in {"expired", "delete", "missing", "newer"}:
        stored["leaseExpiresAt"] = now - timedelta(seconds=1)
    if case in {"delete", "stuck_delete"}:
        stored["jobType"] = FederationJobType.DELETE_SYNC
        fed["status"] = FederationStatus.DELETING
    if case == "missing":
        storage.federations.documents.clear()
    if case == "newer":
        fed["lastSync"]["jobId"] = PydanticObjectId()
    if case.startswith("legacy"):
        for key in ("leaseToken", "leaseOwner", "leaseExpiresAt", "heartbeatAt"):
            stored.pop(key)
        stored["updatedAt"] = now - timedelta(minutes=11 if case == "legacy_old" else 1)
    if case in {"stuck", "stuck_delete", "recent_parent", "recent_terminal_job"}:
        stored["status"] = FederationJobStatus.FAILED
        stored["updatedAt"] = now - timedelta(minutes=3)
    if case == "recent_parent":
        fed["updatedAt"] = now - timedelta(seconds=30)
    if case == "recent_terminal_job":
        stored["updatedAt"] = now
    if case == "no_job":
        storage.jobs.documents.clear()
    await storage.service.reap_expired_federation_jobs()
    if case == "missing":
        assert not storage.federations.documents
        assert "vectors may remain" in caplog.text
    elif case in {"newer", "legacy_recent", "live", "recent_parent", "recent_terminal_job"}:
        assert fed["syncStatus"] == FederationSyncStatus.SYNCING
    else:
        assert fed["syncStatus"] == FederationSyncStatus.FAILED
        assert fed["status"] == FederationStatus.ACTIVE
    if case in {"expired", "delete", "missing", "newer", "legacy_old"}:
        assert stored["status"] == FederationJobStatus.FAILED
        assert stored.get("leaseToken") is None


async def test_stuck_reaper_pages_federations_and_batches_job_reads(storage, monkeypatch):
    monkeypatch.setattr("registry.services.federation_sync_service._STUCK_FEDERATION_BATCH_SIZE", 2)
    old = datetime.now(UTC) - timedelta(minutes=3)
    for _ in range(5):
        federation = storage.federation.model_copy(deep=True)
        federation.id = PydanticObjectId()
        federation.syncStatus = FederationSyncStatus.SYNCING
        federation.updatedAt = old
        storage.federations.documents[federation.id] = federation.model_dump(by_alias=True)

    count = await storage.service.reap_expired_federation_jobs()

    assert count == 5
    assert len(storage.jobs.distinct_calls) == 3
    assert len(storage.jobs.aggregate_calls) == 3
    assert all(
        doc["syncStatus"] == FederationSyncStatus.FAILED
        for doc in storage.federations.documents.values()
        if doc["updatedAt"] != storage.federation.updatedAt
    )


async def test_stuck_scan_without_job_does_not_clobber_newer_parent(storage):
    old = datetime.now(UTC) - timedelta(minutes=3)
    stored = storage.federations.documents[storage.federation.id]
    stored["syncStatus"] = FederationSyncStatus.SYNCING
    stored["updatedAt"] = old
    stored["updatedAt"] = datetime.now(UTC)
    stored["syncStatus"] = FederationSyncStatus.PENDING

    await storage.service._finalize_abandoned_job(
        storage.federation.id,
        None,
        reason="late scan",
        scanned_updated_at=old,
        from_stuck_scan=True,
    )

    assert stored["syncStatus"] == FederationSyncStatus.PENDING


@pytest.mark.parametrize(
    "state,expected", [("expired", False), ("live", True), ("legacy_old", False), ("legacy_recent", True)]
)
async def test_all_active_readers_ignore_expired_jobs(storage, state, expected):
    job, _ = await _pending(storage)
    stored = storage.jobs.documents[job.id]
    if state == "expired":
        stored["leaseExpiresAt"] = datetime.now(UTC) - timedelta(seconds=1)
    if state.startswith("legacy"):
        stored.pop("leaseToken")
        stored.pop("leaseExpiresAt")
        stored["updatedAt"] = datetime.now(UTC) - timedelta(minutes=11 if state == "legacy_old" else 1)
    service = storage.service.federation_job_service
    assert (await service.get_active_job(storage.federation.id) is not None) is expected
    assert await service.has_active_jobs() is expected
    assert bool(await service.list_active_jobs(limit=5)) is expected


async def test_delete_vector_exception_after_commit_keeps_parent_deleted(storage):
    service = storage.service
    job, lease = await _pending(storage, job_type=FederationJobType.DELETE_SYNC)
    storage.federation.status = FederationStatus.DELETING

    async def delete(*args, **kwargs):
        storage.federations.documents.clear()
        return ["arn:aws:agentcore:region:account:runtime/test"], []

    service._delete_transaction = AsyncMock(side_effect=delete)
    service.mcp_server_repo.ensure_collection = AsyncMock(side_effect=RuntimeError("vector collection unavailable"))
    await service.run_delete(storage.federation, job, lease)
    stored = storage.jobs.documents[job.id]
    assert stored["status"] == FederationJobStatus.SUCCESS
    assert stored["leaseToken"] is None
    assert "vector collection unavailable" in stored["applySummary"]["errorMessages"][0]
    assert not storage.federations.documents


async def test_metadata_update_survives_stale_sync_snapshot(storage):
    job, _ = await _pending(storage)
    stale = storage.federation.model_copy(deep=True)
    crud = storage.service.federation_crud_service
    await crud.update_federation(
        federation=storage.federation,
        display_name="Edited during sync",
        description="new description",
        tags=["new"],
        provider_config=storage.federation.providerConfig,
        updated_by="editor",
    )
    assert storage.federations.documents[stale.id]["syncStatus"] == FederationSyncStatus.PENDING
    await crud.mark_sync_success(
        stale,
        FederationLastSync(jobId=job.id, status=FederationSyncStatus.SUCCESS),
        FederationStats(),
        job_id=job.id,
    )
    stored = storage.federations.documents[stale.id]
    assert stored["displayName"] == "Edited during sync"
    assert stored["description"] == "new description"
    assert stored["tags"] == ["new"]
    assert stored["updatedBy"] == "editor"
    assert stored["syncStatus"] == FederationSyncStatus.SUCCESS


async def test_stale_snapshot_cannot_admit_second_sync_or_delete(storage):
    stale = storage.federation.model_copy(deep=True)
    stale_delete = stale.model_copy(deep=True)
    job, _ = await _pending(storage)
    with pytest.raises(ValueError, match="changed concurrently"):
        await storage.service.federation_crud_service.mark_sync_pending(stale)
    with pytest.raises(ValueError, match="changed concurrently"):
        await storage.service.federation_crud_service.mark_deleting(stale_delete, job_id=PydanticObjectId())
    stored = storage.federations.documents[stale.id]
    assert stored["status"] == FederationStatus.ACTIVE
    assert stored["syncStatus"] == FederationSyncStatus.PENDING
    assert stored["lastSync"]["jobId"] == job.id


async def test_late_sync_terminal_writes_cannot_change_deleting_parent(storage):
    job, _ = await _pending(storage)
    stale_success = storage.federation.model_copy(deep=True)
    stale_failure = storage.federation.model_copy(deep=True)
    stored = storage.federations.documents[storage.federation.id]
    stored["status"] = FederationStatus.DELETING
    stored["deletingJobId"] = PydanticObjectId()

    await storage.service.federation_crud_service.mark_sync_success(
        stale_success,
        FederationLastSync(jobId=job.id, status=FederationSyncStatus.SUCCESS),
        FederationStats(),
        job_id=job.id,
    )
    await storage.service.federation_crud_service.mark_sync_failed(
        stale_failure,
        "late failure",
        job_id=job.id,
    )

    assert stored["status"] == FederationStatus.DELETING
    assert stored["syncStatus"] == FederationSyncStatus.PENDING
    assert stored["lastSync"]["jobId"] == job.id


async def test_old_delete_finalizer_cannot_restore_new_delete(storage):
    crud = storage.service.federation_crud_service
    old_id, new_id = PydanticObjectId(), PydanticObjectId()
    old_job = FederationSyncJob(id=old_id, federationId=storage.federation.id, jobType=FederationJobType.DELETE_SYNC)
    await crud.mark_deleting(storage.federation, job_id=old_id)
    await crud.mark_delete_failed(storage.federation, "old failure", expected_job_id=old_id)
    await crud.mark_deleting(storage.federation, job_id=new_id)

    await storage.service._finalize_abandoned_job(storage.federation.id, old_job, reason="late reaper")

    stored = storage.federations.documents[storage.federation.id]
    assert stored["status"] == FederationStatus.DELETING
    assert stored["deletingJobId"] == new_id
    assert stored["syncStatus"] == FederationSyncStatus.PENDING


async def test_stuck_scan_cannot_restore_replaced_delete(storage):
    crud = storage.service.federation_crud_service
    old_id, new_id = PydanticObjectId(), PydanticObjectId()
    await crud.mark_deleting(storage.federation, job_id=old_id)
    await crud.mark_delete_failed(storage.federation, "old failure", expected_job_id=old_id)
    await crud.mark_deleting(storage.federation, job_id=new_id)

    await storage.service._finalize_abandoned_job(
        storage.federation.id,
        None,
        reason="late scan",
        scanned_delete_job_id=old_id,
        from_stuck_scan=True,
    )

    stored = storage.federations.documents[storage.federation.id]
    assert stored["status"] == FederationStatus.DELETING
    assert stored["deletingJobId"] == new_id


async def test_deleted_parent_is_never_upserted(storage):
    storage.federation.status = FederationStatus.DELETING
    storage.federation.syncStatus = FederationSyncStatus.SYNCING
    storage.federations.documents.clear()
    await storage.service.federation_crud_service.mark_delete_failed(
        storage.federation, "failure", expected_job_id=storage.federation.deletingJobId
    )
    assert not storage.federations.documents
