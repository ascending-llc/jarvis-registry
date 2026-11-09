"""Real-Mongo coverage: a skill write enqueues exactly one scan job on commit, none on rollback.

Gated on MONGO_INTEGRATION_URI (a replica set — transactions require one), like
test_federation_sync_transaction.py. The acl/user services are mocked; only the skill and scan
collections are real, so the enqueue's transactional behaviour is what is under test.
"""

import os
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from beanie import PydanticObjectId, init_beanie
from pymongo import AsyncMongoClient

from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.skill_api_schemas import SkillCreateRequest, SkillUpdateRequest
from registry.services.skill_service import SkillService
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedSkill, ExtendedSkillFile, SkillScanJob, SkillScanResult
from registry_pkgs.models.enums import SkillScanJobStatus, SkillScanTriggerType
from registry_pkgs.skill_scan.enqueue import enqueue_skill_scan

pytestmark = pytest.mark.integration

_USER_ID = "000000000000000000000001"


@pytest_asyncio.fixture
async def ctx(monkeypatch) -> AsyncGenerator[SimpleNamespace, None]:
    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI to a replica-set MongoDB for real enqueue tests")

    database_name = f"skill_scan_enqueue_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    database = client[database_name]
    monkeypatch.setattr(MongoDB, "get_client", lambda: client)

    try:
        await init_beanie(
            database=database,
            document_models=[ExtendedSkill, ExtendedSkillFile, SkillScanJob, SkillScanResult],
        )
        acl = MagicMock()
        acl.grant_permission = AsyncMock()
        acl.delete_acl_entries_for_resource = AsyncMock(return_value=0)
        acl.check_user_permission = AsyncMock(
            return_value=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)
        )
        user_service = MagicMock()
        user_service.get_user_by_user_id = AsyncMock(
            return_value=SimpleNamespace(name="Integration User", username="integration")
        )
        service = SkillService(acl_service=acl, user_service=user_service)
        yield SimpleNamespace(service=service, database=database)
    finally:
        await client.drop_database(database_name)
        await client.close()


async def _jobs_for(skill_id: PydanticObjectId) -> list[dict]:
    return await SkillScanJob.get_pymongo_collection().find({"skillId": skill_id}).to_list(length=None)


@pytest.mark.asyncio
async def test_create_skill_commits_exactly_one_pending_job(ctx):
    skill, _files, _perms = await ctx.service.create_skill(
        SkillCreateRequest(name="alpha", description="d"), _USER_ID, "Integration User"
    )
    jobs = await _jobs_for(skill.id)
    assert len(jobs) == 1
    assert jobs[0]["status"] == SkillScanJobStatus.PENDING.value


@pytest.mark.asyncio
async def test_rollback_after_enqueue_leaves_job_and_skill_untouched(ctx, monkeypatch):
    # The enqueue writes skill_scan_jobs inside the update transaction. To prove that write rolls back
    # with the skill, force the failure AFTER the real enqueue has run (a pre-save 409 would never
    # reach it, so it would pass even with session=None).
    skill, _f, _p = await ctx.service.create_skill(SkillCreateRequest(name="movable", description="d"), _USER_ID, "x")
    before_version = skill.version
    # Flip the job to DONE so a committed enqueue would visibly reset it to PENDING -> if we still see
    # DONE after the rollback, the enqueue's in-transaction write was undone.
    await SkillScanJob.get_pymongo_collection().update_one(
        {"skillId": skill.id}, {"$set": {"status": SkillScanJobStatus.DONE.value}}
    )
    before = await _jobs_for(skill.id)
    assert len(before) == 1

    real_enqueue = enqueue_skill_scan

    async def fail_after_enqueue(skill_id, *, trigger: SkillScanTriggerType, requested_by, session) -> None:
        await real_enqueue(skill_id, trigger=trigger, requested_by=requested_by, session=session)
        raise RuntimeError("Forced rollback after enqueue")

    monkeypatch.setattr("registry.services.skill_service.enqueue_skill_scan", fail_after_enqueue)

    with pytest.raises(RuntimeError, match="Forced rollback after enqueue"):
        await ctx.service.update_skill(skill.id, SkillUpdateRequest(description="Changed"), _USER_ID)

    assert await _jobs_for(skill.id) == before  # job write (incl. the DONE->PENDING reset) rolled back
    stored = await ExtendedSkill.get_pymongo_collection().find_one({"_id": skill.id})
    assert stored is not None
    assert stored["description"] == "d"  # the skill edit rolled back too
    assert stored["version"] == before_version


@pytest.mark.asyncio
async def test_toggle_does_not_enqueue(ctx):
    skill, _f, _p = await ctx.service.create_skill(SkillCreateRequest(name="toggler", description="d"), _USER_ID, "x")
    await SkillScanJob.get_pymongo_collection().delete_many({"skillId": skill.id})
    await ctx.service.toggle_skill(skill.id, False, _USER_ID)
    assert await _jobs_for(skill.id) == []
