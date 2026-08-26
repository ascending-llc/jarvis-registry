from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.models.enums import (
    SkillSyncJobErrorCode,
    SkillSyncJobPhase,
    SkillSyncJobStateMachine,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncTriggerType,
)
from registry_pkgs.models.skill_sync_job import SkillSyncJob, SkillSyncRequestSnapshot

_MAX_JOB_ATTEMPTS = 3


class SkillSyncJobService:
    """Own durable job persistence, claiming, leases, and retry exhaustion.

    MongoDB conditional updates in this service are the worker-concurrency boundary:
    they select one runnable job, establish or renew its owner lease, and terminally fail
    abandoned jobs after the retry limit. This service does not create background tasks or
    decide the business outcome of a sync pipeline.
    """

    async def claim_next_job(
        self,
        *,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> SkillSyncJob | None:
        """Atomically claim the oldest runnable job and establish a renewable worker lease.

        ``find_one_and_update`` is the concurrency boundary: competing registry instances
        cannot both observe the same job as claimable. Expired syncing jobs are eligible for
        recovery until the attempt limit is reached.
        """
        now = datetime.now(UTC)
        collection = SkillSyncJob.get_pymongo_collection()
        document = await collection.find_one_and_update(
            {
                "$and": [
                    {
                        "$or": [
                            {"attemptCount": {"$lt": _MAX_JOB_ATTEMPTS}},
                            {"attemptCount": {"$exists": False}},
                        ]
                    },
                    {
                        "$or": [
                            {
                                "status": SkillSyncJobStatus.PENDING.value,
                                "$or": [
                                    {"leaseExpiresAt": None},
                                    {"leaseExpiresAt": {"$exists": False}},
                                    {"leaseExpiresAt": {"$lte": now}},
                                ],
                            },
                            {
                                "status": SkillSyncJobStatus.SYNCING.value,
                                "leaseExpiresAt": {"$lte": now},
                            },
                        ]
                    },
                ]
            },
            {
                "$set": {
                    "status": SkillSyncJobStatus.SYNCING.value,
                    "leaseOwner": lease_owner,
                    "leaseExpiresAt": now + lease_duration,
                    "heartbeatAt": now,
                    "updatedAt": now,
                },
                "$inc": {"attemptCount": 1},
            },
            sort=[("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return SkillSyncJob.model_validate(document) if document is not None else None

    async def heartbeat(
        self,
        *,
        job_id: PydanticObjectId,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> bool:
        """Renew a lease only while the caller still owns the syncing job."""
        now = datetime.now(UTC)
        result = await SkillSyncJob.get_pymongo_collection().update_one(
            {
                "_id": job_id,
                "status": SkillSyncJobStatus.SYNCING.value,
                "leaseOwner": lease_owner,
            },
            {
                "$set": {
                    "leaseExpiresAt": now + lease_duration,
                    "heartbeatAt": now,
                    "updatedAt": now,
                }
            },
        )
        return result.modified_count == 1

    async def fail_next_exhausted_job(self) -> SkillSyncJob | None:
        """Finalize one expired job that exhausted retries so it cannot block its source forever."""
        now = datetime.now(UTC)
        document = await SkillSyncJob.get_pymongo_collection().find_one_and_update(
            {
                "status": {"$in": [SkillSyncJobStatus.PENDING.value, SkillSyncJobStatus.SYNCING.value]},
                "attemptCount": {"$gte": _MAX_JOB_ATTEMPTS},
                "leaseExpiresAt": {"$lte": now},
            },
            {
                "$set": {
                    "status": SkillSyncJobStatus.FAILED.value,
                    "phase": SkillSyncJobPhase.FAILED.value,
                    "errorCode": SkillSyncJobErrorCode.INTERNAL_ERROR.value,
                    "error": f"Job abandoned after {_MAX_JOB_ATTEMPTS} worker attempts",
                    "finishedAt": now,
                    "leaseOwner": None,
                    "leaseExpiresAt": None,
                    "updatedAt": now,
                }
            },
            sort=[("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return SkillSyncJob.model_validate(document) if document is not None else None

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
        request_snapshot: SkillSyncRequestSnapshot,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncJob:
        """Persist one typed execution request after enforcing the per-source active-job guard."""
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
