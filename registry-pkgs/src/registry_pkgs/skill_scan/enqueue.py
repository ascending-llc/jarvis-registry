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

    ``rerequested`` is a promise to the requester: whoever ends a SCANNING job must honour it. Every exit
    from SCANNING (``done``, and ``failed`` on the last attempt or by the exhausted-job reaper) must, when
    ``rerequested`` is true, go to PENDING with ``attemptCount`` 0 and ``rerequested`` false instead, using
    ``LeasedRepository.transition(..., extra_filter={"rerequested": ...})`` so the check is atomic. Otherwise a
    request that arrives during the last attempt is silently dropped.

    The ``skill_scan_jobs`` unique index on ``skillId`` means two concurrent transactions enqueuing the same
    skill before its job exists will collide (one aborts with a WriteConflict). The caller must handle that
    WriteConflict: retry the transaction (e.g. ``with_transaction``, as skill sync does) or surface it as 409
    (as ``skill_service`` does for its other write conflicts).
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
