"""Real-Mongo lifecycle tests (needs a replica set for transactions).

Set MONGO_INTEGRATION_URI to run; skipped otherwise. Pattern mirrors registry-pkgs/tests/integration.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from beanie import PydanticObjectId, init_beanie
from pymongo import AsyncMongoClient
from skill_scan_worker.finish import finish_job
from skill_scan_worker.main import _on_execution_error
from skill_scan_worker.sweeper import OrphanSweeper, fail_exhausted_jobs

from registry_pkgs.database.leased_job import LeaseLostError
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.database.skill_scan_job_repository import (
    claimable_skill_scan_job_filter,
    skill_scan_job_repository,
)
from registry_pkgs.models.enums import SkillScanResultStatus, SkillScanTriggerType
from registry_pkgs.models.extended_skill import ExtendedSkill
from registry_pkgs.models.skill_scan_job import SkillScanJob
from registry_pkgs.models.skill_scan_result import SkillScanResult
from registry_pkgs.skill_scan.enqueue import enqueue_skill_scan

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def mongo():
    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI to run real-Mongo lifecycle tests")
    db_name = f"skill_scan_worker_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    await init_beanie(database=client[db_name], document_models=[SkillScanJob, SkillScanResult, ExtendedSkill])
    MongoDB.client = client
    MongoDB.database_name = db_name
    try:
        yield client
    finally:
        MongoDB.client = None
        await client.drop_database(db_name)
        await client.close()


def _jobs():
    return SkillScanJob.get_pymongo_collection()


def _results():
    return SkillScanResult.get_pymongo_collection()


def _skill(skill_id: PydanticObjectId) -> ExtendedSkill:
    skill = ExtendedSkill(name="s", description="d", author=PydanticObjectId(), authorName="a")
    skill.id = skill_id
    return skill


def _result(skill_id: PydanticObjectId, digest: str, *, tool: str = "t", fingerprint: str = "fp") -> SkillScanResult:
    return SkillScanResult(
        skillId=skill_id,
        contentDigest=digest,
        skillVersion=1,
        tool=tool,
        toolVersion="1.0.0",
        toolFingerprint=fingerprint,
        status=SkillScanResultStatus.SUCCEEDED,
        findings=[],
    )


async def _enqueue_and_claim(skill_id: PydanticObjectId, *, prior_attempts: int = 0):
    """Enqueue a job, optionally mark earlier attempts as used, and claim it (the claim adds one attempt)."""
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    if prior_attempts:
        await _jobs().update_one({"skillId": skill_id}, {"$set": {"attemptCount": prior_attempts}})
    claimed = await _claim(skill_id)
    assert claimed is not None
    return claimed


async def _claim(skill_id: PydanticObjectId):
    now = datetime.now(UTC)
    return await skill_scan_job_repository.claim(
        owner="owner-a",
        claimable_filter=claimable_skill_scan_job_filter(now),
        set_fields={"status": "scanning", "startedAt": now, "rerequested": False},
        inc={"attemptCount": 1},
        sort=[("requestedAt", 1)],
    )


async def test_finish_done(mongo) -> None:
    skill_id = PydanticObjectId()
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    claimed = await _claim(skill_id)
    assert claimed is not None
    _job, lease = claimed

    await finish_job(lease, _skill(skill_id), "sha256:d1", {"t": "fp"}, [_result(skill_id, "sha256:d1")])

    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "done"
    assert job.get("summary") is not None
    assert await _results().count_documents({"skillId": skill_id, "contentDigest": "sha256:d1"}) == 1


async def test_finish_requeues_when_rerequested_during_scan(mongo) -> None:
    skill_id = PydanticObjectId()
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    claimed = await _claim(skill_id)
    assert claimed is not None
    _job, lease = claimed
    await _jobs().update_one({"skillId": skill_id}, {"$set": {"rerequested": True}})

    await finish_job(lease, _skill(skill_id), "sha256:d1", {"t": "fp"}, [_result(skill_id, "sha256:d1")])

    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "pending"
    assert job["rerequested"] is False
    assert job["attemptCount"] == 0


async def test_finish_after_lease_loss_writes_nothing(mongo) -> None:
    skill_id = PydanticObjectId()
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    claimed = await _claim(skill_id)
    assert claimed is not None
    _job, lease = claimed
    # Another owner re-claims: the token changes, so our fence no longer matches.
    await _jobs().update_one({"skillId": skill_id}, {"$set": {"leaseToken": "stolen"}})

    with pytest.raises(LeaseLostError):
        await finish_job(lease, _skill(skill_id), "sha256:d1", {"t": "fp"}, [_result(skill_id, "sha256:d1")])

    assert await _results().count_documents({"skillId": skill_id}) == 0


async def test_finish_deletes_old_digest_results(mongo) -> None:
    skill_id = PydanticObjectId()
    await _results().insert_one(_result(skill_id, "sha256:old").model_dump(mode="python", exclude={"id"}))
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    claimed = await _claim(skill_id)
    assert claimed is not None
    _job, lease = claimed

    await finish_job(lease, _skill(skill_id), "sha256:new", {"t": "fp"}, [_result(skill_id, "sha256:new")])

    assert await _results().count_documents({"skillId": skill_id, "contentDigest": "sha256:old"}) == 0
    assert await _results().count_documents({"skillId": skill_id, "contentDigest": "sha256:new"}) == 1


async def test_finish_succeeds_under_concurrent_heartbeats(mongo) -> None:
    # The finish transaction and the lease heartbeat both write the job doc; a heartbeat landing mid-transaction
    # must not corrupt the result. with_transaction retries the WriteConflict; heartbeat failures are tolerated.
    skill_id = PydanticObjectId()
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
    claimed = await _claim(skill_id)
    assert claimed is not None
    _job, lease = claimed

    stop = asyncio.Event()

    async def _heartbeat_storm() -> None:
        # Paced: back-to-back heartbeats would make every transaction retry conflict (a livelock no real 30 s
        # heartbeat can cause), while a few ms apart they still land mid-transaction.
        while not stop.is_set():
            await skill_scan_job_repository.heartbeat(lease)
            await asyncio.sleep(0.02)

    storm = asyncio.ensure_future(_heartbeat_storm())
    try:
        await finish_job(lease, _skill(skill_id), "sha256:d1", {"t": "fp"}, [_result(skill_id, "sha256:d1")])
    finally:
        stop.set()
        await storm

    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "done"
    assert await _results().count_documents({"skillId": skill_id, "contentDigest": "sha256:d1"}) == 1


async def _insert_exhausted_scanning(skill_id: PydanticObjectId, *, rerequested: bool) -> None:
    now = datetime.now(UTC)
    await _jobs().insert_one(
        {
            "_id": PydanticObjectId(),
            "skillId": skill_id,
            "status": "scanning",
            "rerequested": rerequested,
            "attemptCount": 3,
            "triggerType": "auto",
            "requestedAt": now,
            "createdAt": now,
            "updatedAt": now,
            "leaseOwner": "dead",
            "leaseToken": "dead",
            "leaseExpiresAt": now - timedelta(minutes=1),
        }
    )


async def test_reaper_fails_exhausted_job(mongo) -> None:
    skill_id = PydanticObjectId()
    await _insert_exhausted_scanning(skill_id, rerequested=False)
    await fail_exhausted_jobs()
    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "failed"
    assert job["errorCode"] == "attempts_exhausted"


async def test_reaper_requeues_rerequested_exhausted_job(mongo) -> None:
    skill_id = PydanticObjectId()
    await _insert_exhausted_scanning(skill_id, rerequested=True)
    await fail_exhausted_jobs()
    job = await _jobs().find_one({"skillId": skill_id})
    assert job["status"] == "pending"
    assert job["attemptCount"] == 0
    assert job["rerequested"] is False


async def test_finish_after_job_deleted_writes_nothing(mongo) -> None:
    skill_id = PydanticObjectId()
    _job, lease = await _enqueue_and_claim(skill_id)
    # Registry deletes the job when the skill is deleted mid-scan.
    await _jobs().delete_one({"skillId": skill_id})

    with pytest.raises(LeaseLostError):
        await finish_job(lease, _skill(skill_id), "sha256:d1", {"t": "fp"}, [_result(skill_id, "sha256:d1")])

    assert await _results().count_documents({"skillId": skill_id}) == 0


async def test_finish_drops_old_fingerprint_only_for_tools_that_ran(mongo) -> None:
    skill_id = PydanticObjectId()
    for tool in ("ran", "idle"):
        await _results().insert_one(
            _result(skill_id, "sha256:d1", tool=tool, fingerprint=f"{tool}-old").model_dump(
                mode="python", exclude={"id"}
            )
        )
    _job, lease = await _enqueue_and_claim(skill_id)

    await finish_job(
        lease,
        _skill(skill_id),
        "sha256:d1",
        {"ran": "ran-new", "idle": "idle-new"},
        [_result(skill_id, "sha256:d1", tool="ran", fingerprint="ran-new")],
    )

    fingerprints = {doc["toolFingerprint"] async for doc in _results().find({"skillId": skill_id})}
    assert fingerprints == {"ran-new", "idle-old"}


async def test_execution_error_on_last_attempt_fails_the_job(mongo) -> None:
    skill_id = PydanticObjectId()
    job, lease = await _enqueue_and_claim(skill_id, prior_attempts=2)
    assert job.attemptCount == 3

    await _on_execution_error(job, lease, RuntimeError("boom"))

    doc = await _jobs().find_one({"skillId": skill_id})
    assert doc["status"] == "failed"
    assert doc["errorCode"] == "internal_error"
    assert doc["error"] == "boom"


async def test_execution_error_on_last_attempt_after_rerequest_requeues(mongo) -> None:
    skill_id = PydanticObjectId()
    job, lease = await _enqueue_and_claim(skill_id, prior_attempts=2)
    await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)

    await _on_execution_error(job, lease, RuntimeError("boom"))

    doc = await _jobs().find_one({"skillId": skill_id})
    assert doc["status"] == "pending"
    assert doc["attemptCount"] == 0
    assert doc["rerequested"] is False
    assert "errorCode" not in doc
    assert "error" not in doc


async def test_finish_after_an_earlier_internal_error_clears_the_error(mongo) -> None:
    skill_id = PydanticObjectId()
    job, lease = await _enqueue_and_claim(skill_id)
    await _on_execution_error(job, lease, RuntimeError("boom"))
    assert (await _jobs().find_one({"skillId": skill_id}))["errorCode"] == "internal_error"

    claimed = await _claim(skill_id)
    assert claimed is not None
    _job, lease = claimed
    await finish_job(lease, _skill(skill_id), "sha256:d1", {"t": "fp"}, [_result(skill_id, "sha256:d1")])

    doc = await _jobs().find_one({"skillId": skill_id})
    assert doc["status"] == "done"
    assert "errorCode" not in doc
    assert "error" not in doc


async def test_orphan_sweep_deletes_state_only_for_missing_skills(mongo) -> None:
    kept, orphan = PydanticObjectId(), PydanticObjectId()
    await ExtendedSkill.get_pymongo_collection().insert_one(
        {"_id": kept, "name": "s", "description": "d", "author": PydanticObjectId(), "authorName": "a"}
    )
    for skill_id in (kept, orphan):
        await enqueue_skill_scan(skill_id, trigger=SkillScanTriggerType.AUTO, requested_by=None, session=None)
        await _results().insert_one(_result(skill_id, "sha256:d1").model_dump(mode="python", exclude={"id"}))

    await OrphanSweeper("owner-a", 60)._sweep()

    for collection in (_jobs(), _results()):
        assert await collection.count_documents({"skillId": kept}) == 1
        assert await collection.count_documents({"skillId": orphan}) == 0
