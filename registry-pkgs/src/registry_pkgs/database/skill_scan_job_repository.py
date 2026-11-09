"""Lease spec and claimable filter for SkillScanJob, mirroring skill_sync_job_service."""

from datetime import datetime, timedelta
from typing import Any

from ..models.enums import SkillScanJobStatus
from ..models.skill_scan_job import SkillScanJob
from .leased_job import CAMEL_CASE_LEASE_FIELDS, LeasedRepository, LeaseSpec

SKILL_SCAN_JOB_MAX_ATTEMPTS = 3

SKILL_SCAN_JOB_LEASE: LeaseSpec[SkillScanJob] = LeaseSpec(
    document=SkillScanJob,
    fields=CAMEL_CASE_LEASE_FIELDS,
    status_field="status",
    leased_statuses=frozenset({SkillScanJobStatus.SCANNING.value}),
    duration=timedelta(minutes=2),
    heartbeat_interval=timedelta(seconds=30),
)

skill_scan_job_repository: LeasedRepository[SkillScanJob] = LeasedRepository(SKILL_SCAN_JOB_LEASE)


def claimable_skill_scan_job_filter(now: datetime) -> dict[str, Any]:
    """A job is claimable when PENDING, or SCANNING with an expired lease, and under the attempt cap."""
    return {
        "$and": [
            {
                "$or": [
                    {"attemptCount": {"$lt": SKILL_SCAN_JOB_MAX_ATTEMPTS}},
                    {"attemptCount": {"$exists": False}},
                ]
            },
            {
                "$or": [
                    {
                        "status": SkillScanJobStatus.PENDING.value,
                        "$or": [
                            {"leaseExpiresAt": None},
                            {"leaseExpiresAt": {"$exists": False}},
                            {"leaseExpiresAt": {"$lte": now}},
                        ],
                    },
                    {
                        "status": SkillScanJobStatus.SCANNING.value,
                        "leaseExpiresAt": {"$lte": now},
                    },
                ]
            },
        ]
    }
