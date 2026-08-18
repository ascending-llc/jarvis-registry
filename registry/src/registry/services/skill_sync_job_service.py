from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.models.enums import (
    SkillSyncJobErrorCode,
    SkillSyncJobPhase,
    SkillSyncJobStateMachine,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncTriggerType,
)
from registry_pkgs.models.skill_sync_job import SkillSyncJob


class SkillSyncJobService:
    async def get_job(self, job_id: str, *, source_id: PydanticObjectId) -> SkillSyncJob | None:
        try:
            object_id = PydanticObjectId(job_id)
        except (InvalidId, TypeError, ValueError):
            return None
        return await SkillSyncJob.find_one({"_id": object_id, "sourceId": source_id})

    async def get_active_job(
        self,
        source_id: PydanticObjectId,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncJob | None:
        return await SkillSyncJob.find_one(
            {
                "sourceId": source_id,
                "status": {"$in": [SkillSyncJobStatus.PENDING.value, SkillSyncJobStatus.SYNCING.value]},
            },
            sort=[("createdAt", -1)],
            session=session,
        )

    async def create_job(
        self,
        *,
        source_id: PydanticObjectId,
        job_type: SkillSyncJobType,
        trigger_type: SkillSyncTriggerType,
        triggered_by: str,
        request_snapshot: dict[str, Any],
        session: AsyncClientSession | None = None,
    ) -> SkillSyncJob:
        if await self.get_active_job(source_id, session=session):
            raise ValueError("Skill sync source already has an active sync job")
        job = SkillSyncJob(
            sourceId=source_id,
            jobType=job_type,
            triggerType=trigger_type,
            triggeredBy=triggered_by,
            requestSnapshot=request_snapshot,
        )
        await job.insert(session=session)
        return job

    async def mark_not_implemented(self, job: SkillSyncJob) -> SkillSyncJob:
        job.status = SkillSyncJobStateMachine.transition_to_failed(job.status)
        job.phase = SkillSyncJobPhase.FAILED
        job.errorCode = SkillSyncJobErrorCode.SYNC_NOT_IMPLEMENTED.value
        job.error = "Skill sync execution is not implemented yet"
        job.finishedAt = datetime.now(UTC)
        await job.save()
        return job
