"""Transactional enqueue for skill scans.

EVERY write path that changes a skill's frontmatter, body or files must call this with the write's
transaction session, so the enqueue commits or rolls back together with the skill write.
"""

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from ..models.enums import SkillScanTriggerType
from ..models.skill_scan_job import SkillScanJob


async def enqueue_skill_scan(
    skill_id: PydanticObjectId,
    *,
    trigger: SkillScanTriggerType,
    requested_by: str | None,
    session: AsyncClientSession | None,
) -> None:
    """Upsert the skill's job so a scan runs.

    - PENDING / DONE / FAILED / new -> reset to PENDING (attemptCount 0, errors cleared).
    - SCANNING -> left running, only ``rerequested`` is set so the worker runs again when it finishes.

    Lease fields and ``summary`` are never touched.

    The ``skill_scan_jobs`` unique index on ``skillId`` means two concurrent transactions enqueuing the same
    skill before its job exists will collide (one aborts with a WriteConflict). The caller's transaction must
    therefore be retried on TransientTransactionError / WriteConflict, as the skill write paths already are.
    """
    scanning = {"$eq": ["$status", "scanning"]}
    pipeline = [
        {
            "$set": {
                "skillId": skill_id,
                "status": {"$cond": [scanning, "scanning", "pending"]},
                "rerequested": {"$cond": [scanning, True, False]},
                "attemptCount": {"$cond": [scanning, "$attemptCount", 0]},
                "errorCode": {"$cond": [scanning, "$errorCode", "$$REMOVE"]},
                "error": {"$cond": [scanning, "$error", "$$REMOVE"]},
                "triggerType": trigger.value,
                "requestedBy": requested_by,
                "requestedAt": "$$NOW",
                "updatedAt": "$$NOW",
                "createdAt": {"$ifNull": ["$createdAt", "$$NOW"]},
            }
        }
    ]
    await SkillScanJob.get_pymongo_collection().update_one(
        {"skillId": skill_id}, pipeline, upsert=True, session=session
    )
