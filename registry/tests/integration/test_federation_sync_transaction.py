"""Real-Mongo coverage for federation job admission and transaction rollback."""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from beanie import init_beanie
from pymongo import AsyncMongoClient

from registry.services.federation_crud_service import FederationCrudService
from registry.services.federation_job_service import FederationJobService
from registry.services.federation_sync_service import FederationSyncService
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.enums import (
    FederationJobStatus,
    FederationJobType,
    FederationProviderType,
    FederationStatus,
    FederationSyncStatus,
    FederationTriggerType,
)
from registry_pkgs.models.federation import Federation
from registry_pkgs.models.federation_sync_job import FederationSyncJob


@pytest_asyncio.fixture
async def mongo_federation_context(monkeypatch) -> AsyncGenerator[SimpleNamespace, None]:
    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI to a replica-set MongoDB for real federation tests")

    database_name = f"federation_admission_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    database = client[database_name]
    monkeypatch.setattr(MongoDB, "get_client", lambda: client)

    try:
        await init_beanie(database=database, document_models=[Federation, FederationSyncJob])
        service = FederationSyncService(
            federation_crud_service=FederationCrudService(),
            federation_job_service=FederationJobService(),
            mcp_server_repo=MagicMock(),
            a2a_agent_repo=MagicMock(),
            acl_service=MagicMock(),
            user_service=MagicMock(),
            azure_client_cache=MagicMock(),
            embedding_maintenance_watcher=SimpleNamespace(is_active=lambda: False),
            lease_owner="admission-test",
        )
        yield SimpleNamespace(client=client, database=database, service=service)
    finally:
        await client.drop_database(database_name)
        await client.close()


@pytest.mark.asyncio
async def test_delete_admission_rolls_back_job_on_parent_state_conflict(mongo_federation_context):
    database = mongo_federation_context.database
    service = mongo_federation_context.service
    federation = Federation(providerType=FederationProviderType.AWS_AGENTCORE, displayName="Admission test")
    await federation.insert()
    stale = federation.model_copy(deep=True)
    await database.federations.update_one(
        {"_id": federation.id}, {"$set": {"syncStatus": FederationSyncStatus.PENDING.value}}
    )

    # Admission is under test here; close the unstarted execution coroutine deliberately.
    service.execute_leased_federation_job = AsyncMock(side_effect=lambda **kwargs: kwargs["coro"].close())

    with pytest.raises(ValueError, match="Federation changed concurrently"):
        await service.start_delete(federation=stale, triggered_by="tester")

    assert await database.federation_sync_jobs.count_documents({"federationId": federation.id}) == 0
    stored = await database.federations.find_one({"_id": federation.id})
    assert stored["status"] == FederationStatus.ACTIVE.value
    assert stored["syncStatus"] == FederationSyncStatus.PENDING.value

    await database.federations.update_one(
        {"_id": federation.id}, {"$set": {"syncStatus": FederationSyncStatus.IDLE.value}}
    )
    fresh = await Federation.get(federation.id)
    job = await service.start_delete(federation=fresh, triggered_by="tester")

    stored_job = await database.federation_sync_jobs.find_one({"_id": job.id})
    stored = await database.federations.find_one({"_id": federation.id})
    assert stored_job["status"] == FederationJobStatus.PENDING.value
    assert stored_job["leaseToken"]
    assert stored["status"] == FederationStatus.DELETING.value
    assert stored["deletingJobId"] == job.id
    service.execute_leased_federation_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_stuck_reaper_batches_recent_jobs_with_real_mongo(mongo_federation_context):
    database = mongo_federation_context.database
    service = mongo_federation_context.service
    federation = Federation(providerType=FederationProviderType.AWS_AGENTCORE, displayName="Stuck test")
    await federation.insert()
    job, _lease = await service.federation_job_service.create_job(
        federation_id=federation.id,
        job_type=FederationJobType.FULL_SYNC,
        trigger_type=FederationTriggerType.MANUAL,
        triggered_by="tester",
        request_snapshot={},
        lease_owner="admission-test",
    )
    old = datetime.now(UTC) - timedelta(minutes=3)
    await database.federations.update_one(
        {"_id": federation.id},
        {"$set": {"syncStatus": FederationSyncStatus.SYNCING.value, "lastSync": {"jobId": job.id}, "updatedAt": old}},
    )
    await database.federation_sync_jobs.update_one(
        {"_id": job.id},
        {"$set": {"status": FederationJobStatus.FAILED.value, "updatedAt": old, "leaseToken": None}},
    )

    assert await service.reap_expired_federation_jobs() == 1
    stored = await database.federations.find_one({"_id": federation.id})
    assert stored["syncStatus"] == FederationSyncStatus.FAILED.value
    assert stored["lastSync"]["jobId"] == job.id


@pytest.mark.asyncio
async def test_stale_delete_transaction_cannot_remove_new_owner(mongo_federation_context, monkeypatch):
    client = mongo_federation_context.client
    database = mongo_federation_context.database
    service = mongo_federation_context.service
    federation = Federation(providerType=FederationProviderType.AWS_AGENTCORE, displayName="Delete owner test")
    await federation.insert()
    old_job, _old_lease = await service.federation_job_service.create_job(
        federation_id=federation.id,
        job_type=FederationJobType.DELETE_SYNC,
        trigger_type=FederationTriggerType.MANUAL,
        triggered_by="tester",
        request_snapshot={},
        lease_owner="old-owner",
    )
    await service.federation_crud_service.mark_deleting(federation, job_id=old_job.id)
    await service.federation_crud_service.mark_delete_failed(federation, "old failure", expected_job_id=old_job.id)
    new_job, _new_lease = await service.federation_job_service.create_job(
        federation_id=federation.id,
        job_type=FederationJobType.DELETE_SYNC,
        trigger_type=FederationTriggerType.MANUAL,
        triggered_by="tester",
        request_snapshot={},
        lease_owner="new-owner",
    )
    await service.federation_crud_service.mark_deleting(federation, job_id=new_job.id)

    empty_children = SimpleNamespace(to_list=AsyncMock(return_value=[]))
    monkeypatch.setattr(ExtendedMCPServer, "find", lambda *_args, **_kwargs: empty_children)
    monkeypatch.setattr(A2AAgent, "find", lambda *_args, **_kwargs: empty_children)
    service.acl_service.delete_acl_entries_for_resource = AsyncMock()

    with pytest.raises(ValueError, match="Federation changed concurrently"):
        async with client.start_session() as session:
            async with await session.start_transaction():
                await service._delete_transaction(federation, current_job_id=old_job.id, session=session)

    stored = await database.federations.find_one({"_id": federation.id})
    assert stored["status"] == FederationStatus.DELETING.value
    assert stored["deletingJobId"] == new_job.id
    assert await database.federation_sync_jobs.find_one({"_id": new_job.id}) is not None
