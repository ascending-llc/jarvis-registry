import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from beanie import PydanticObjectId, init_beanie
from pymongo import AsyncMongoClient

from registry_pkgs.database.leased_job import Lease
from registry_pkgs.database.skill_scan_job_repository import skill_scan_job_repository
from registry_pkgs.models.enums import SkillScanTriggerType
from registry_pkgs.models.skill_scan_job import SkillScanJob
from registry_pkgs.models.skill_scan_result import SkillScanResult
from registry_pkgs.skill_scan.enqueue import enqueue_skill_scan
from registry_pkgs.skill_scan.plan import delete_skill_scan_state

pytestmark = pytest.mark.asyncio


class _Rollback(Exception):
    """Raised to make the transaction context manager abort instead of commit."""


@pytest_asyncio.fixture
async def mongo():
    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI to run real-Mongo skill-scan tests")
    db_name = f"skill_scan_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    await init_beanie(database=client[db_name], document_models=[SkillScanJob, SkillScanResult])
    try:
        yield client
    finally:
        await client.drop_database(db_name)
        await client.close()


def _jobs():
    return SkillScanJob.get_pymongo_collection()


async def _insert_scanning_job(skill_id: PydanticObjectId, **overrides) -> PydanticObjectId:
    now = datetime.now(UTC)
    doc = {
        "_id": PydanticObjectId(),
        "skillId": skill_id,
        "status": "scanning",
        "rerequested": False,
        "attemptCount": 2,
        "triggerType": "auto",
        "requestedAt": now,
        "createdAt": now,
        "updatedAt": now,
        "leaseOwner": "owner",
        "leaseToken": "tok",
        "leaseExpiresAt": now + timedelta(minutes=2),
    }
    doc.update(overrides)
    await _jobs().insert_one(doc)
    return doc["_id"]


async def test_two_enqueues_leave_one_pending_job(mongo):
    skill_id = PydanticObjectId()
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.MANUAL, requested_by="u1", session=None)

    assert await _jobs().count_documents({"skillId": skill_id}) == 1
    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "pending"
    assert job["attemptCount"] == 0
    assert job["rerequested"] is False


async def test_enqueue_in_aborted_transaction_leaves_no_job(mongo):
    skill_id = PydanticObjectId()
    try:
        async with mongo.start_session() as session, await session.start_transaction():
            await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=session)
            raise _Rollback()
    except _Rollback:
        pass
    assert await _jobs().count_documents({"skillId": skill_id}) == 0


async def test_enqueue_on_scanning_job_only_sets_rerequested(mongo):
    skill_id = PydanticObjectId()
    await _insert_scanning_job(skill_id)
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.MANUAL, requested_by="u1", session=None)

    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "scanning"
    assert job["rerequested"] is True
    assert job["attemptCount"] == 2  # unchanged
    assert job["leaseToken"] == "tok"  # lease untouched
    assert job["leaseOwner"] == "owner"


@pytest.mark.parametrize("terminal_status", ["done", "failed"])
async def test_enqueue_on_terminal_job_resets_it(mongo, terminal_status):
    skill_id = PydanticObjectId()
    now = datetime.now(UTC)
    await _jobs().insert_one(
        {
            "_id": PydanticObjectId(),
            "skillId": skill_id,
            "status": terminal_status,
            "rerequested": True,
            "attemptCount": 3,
            "errorCode": "boom",
            "error": "it broke",
            "triggerType": "auto",
            "requestedAt": now,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)

    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "pending"
    assert job["attemptCount"] == 0
    assert job["rerequested"] is False
    assert "errorCode" not in job
    assert "error" not in job


async def test_transition_extra_filter_misses_but_lease_still_owned(mongo):
    skill_id = PydanticObjectId()
    doc_id = await _insert_scanning_job(skill_id, rerequested=True)
    lease = Lease(doc_id=doc_id, owner="owner", token="tok", expires_at=datetime.now(UTC) + timedelta(minutes=2))

    changed = await skill_scan_job_repository.transition(lease, {"status": "done"}, extra_filter={"rerequested": False})
    assert changed is False  # rerequested is True, so the extra filter did not match
    await skill_scan_job_repository.assert_owned(lease)  # lease is still held: must not raise
    job = await _jobs().find_one({"_id": doc_id})
    assert job["status"] == "scanning"  # untouched


async def test_delete_skill_scan_state_commits_and_aborts(mongo):
    skill_id = PydanticObjectId()
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    await SkillScanResult.get_pymongo_collection().insert_one(
        {"skillId": skill_id, "contentDigest": "sha256:x", "toolFingerprint": "fp", "status": "succeeded"}
    )

    # Aborted transaction deletes nothing.
    try:
        async with mongo.start_session() as session, await session.start_transaction():
            await delete_skill_scan_state(skill_id, session=session)
            raise _Rollback()
    except _Rollback:
        pass
    assert await _jobs().count_documents({"skillId": skill_id}) == 1
    assert await SkillScanResult.get_pymongo_collection().count_documents({"skillId": skill_id}) == 1

    # Committed delete removes both.
    await delete_skill_scan_state(skill_id, session=None)
    assert await _jobs().count_documents({"skillId": skill_id}) == 0
    assert await SkillScanResult.get_pymongo_collection().count_documents({"skillId": skill_id}) == 0
