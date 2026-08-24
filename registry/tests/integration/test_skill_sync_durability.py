"""Real-Mongo verification for Skill Sync leasing and transaction durability."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from beanie import PydanticObjectId
from pymongo import AsyncMongoClient

from registry.services.skill_sync_apply_service import SkillSyncApplyService
from registry.services.skill_sync_job_service import SkillSyncJobService
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.enums import (
    SkillSyncJobPhase,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncTriggerType,
)
from registry_pkgs.models.skill_sync_job import SkillSyncJob


@pytest.mark.asyncio
async def test_two_workers_claim_reclaim_and_exhaust_one_job(monkeypatch):
    uri = os.environ.get("SKILL_SYNC_MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set SKILL_SYNC_MONGO_INTEGRATION_URI to run real-Mongo lease tests")

    database_name = f"skill_sync_lease_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    database = client[database_name]
    collection = database.skill_sync_jobs
    monkeypatch.setattr(SkillSyncJob, "get_pymongo_collection", lambda *_args: collection)
    source_id = PydanticObjectId()
    job_id = PydanticObjectId()
    now = datetime.now(UTC)
    await collection.insert_one(
        {
            "_id": job_id,
            "sourceId": source_id,
            "jobType": SkillSyncJobType.FULL_SYNC.value,
            "triggerType": SkillSyncTriggerType.MANUAL.value,
            "triggeredBy": "user-1",
            "status": SkillSyncJobStatus.PENDING.value,
            "phase": SkillSyncJobPhase.QUEUED.value,
            "requestSnapshot": {
                "owner": "octocat",
                "repo": "skills",
                "ref": "main",
                "paths": ["skills"],
                "configRevision": 1,
            },
            "attemptCount": 0,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    service = SkillSyncJobService()

    try:
        first_claims = await asyncio.gather(
            service.claim_next_job(lease_owner="worker-1", lease_duration=timedelta(minutes=2)),
            service.claim_next_job(lease_owner="worker-2", lease_duration=timedelta(minutes=2)),
        )
        claimed = [job for job in first_claims if job is not None]
        assert len(claimed) == 1
        first_owner = claimed[0].leaseOwner
        other_owner = "worker-2" if first_owner == "worker-1" else "worker-1"
        assert (
            await service.heartbeat(
                job_id=job_id,
                lease_owner=other_owner,
                lease_duration=timedelta(minutes=2),
            )
            is False
        )
        assert (
            await service.heartbeat(
                job_id=job_id,
                lease_owner=first_owner,
                lease_duration=timedelta(minutes=2),
            )
            is True
        )

        await collection.update_one({"_id": job_id}, {"$set": {"leaseExpiresAt": now - timedelta(seconds=1)}})
        reclaimed = await service.claim_next_job(
            lease_owner=other_owner,
            lease_duration=timedelta(minutes=2),
        )
        assert reclaimed is not None
        assert reclaimed.leaseOwner == other_owner
        assert reclaimed.attemptCount == 2

        await collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "attemptCount": 3,
                    "leaseExpiresAt": now - timedelta(seconds=1),
                }
            },
        )
        exhausted = await service.fail_next_exhausted_job()
        assert exhausted is not None
        assert exhausted.status == SkillSyncJobStatus.FAILED
        assert exhausted.leaseOwner is None
        assert "3 worker attempts" in exhausted.error
    finally:
        await client.drop_database(database_name)
        await client.close()


@pytest.mark.asyncio
async def test_apply_delete_rolls_back_skill_and_files_when_acl_delete_fails(monkeypatch):
    uri = os.environ.get("SKILL_SYNC_MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set SKILL_SYNC_MONGO_INTEGRATION_URI to run real-Mongo transaction tests")

    database_name = f"skill_sync_apply_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    database = client[database_name]
    skill_id = PydanticObjectId()
    await database.skills.insert_one({"_id": skill_id, "deletedAt": None})
    await database.skillfiles.insert_many(
        [
            {"skillId": skill_id, "relativePath": "one.txt"},
            {"skillId": skill_id, "relativePath": "two.txt"},
        ]
    )

    class _FileFinder:
        async def delete(self, *, session):
            return await database.skillfiles.delete_many({"skillId": skill_id}, session=session)

    class _SkillFileAdapter:
        @staticmethod
        def find(query):
            assert query == {"skillId": skill_id}
            return _FileFinder()

    async def _save_skill(_self, *, session):
        await database.skills.update_one(
            {"_id": skill_id},
            {"$set": {"deletedAt": datetime.now(UTC)}},
            session=session,
        )

    acl_service = type(
        "FailingAclService",
        (),
        {"delete_acl_entries_for_resource": AsyncMock(side_effect=RuntimeError("ACL delete failed"))},
    )()
    skill = type("StoredSkill", (), {"id": skill_id, "deletedAt": None, "save": _save_skill})()
    monkeypatch.setattr(MongoDB, "get_client", lambda: client)
    monkeypatch.setattr("registry.services.skill_sync_apply_service.SkillFile", _SkillFileAdapter)

    try:
        with pytest.raises(RuntimeError, match="ACL delete failed"):
            await SkillSyncApplyService(acl_service)._delete_skill(skill, datetime.now(UTC))

        stored_skill = await database.skills.find_one({"_id": skill_id})
        assert stored_skill["deletedAt"] is None
        assert await database.skillfiles.count_documents({"skillId": skill_id}) == 2
    finally:
        await client.drop_database(database_name)
        await client.close()
