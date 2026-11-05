from datetime import UTC, datetime

from registry_pkgs.database.skill_scan_job_repository import (
    SKILL_SCAN_JOB_LEASE,
    SKILL_SCAN_JOB_MAX_ATTEMPTS,
    claimable_skill_scan_job_filter,
    skill_scan_job_repository,
)
from registry_pkgs.models.skill_scan_job import SkillScanJob


def test_lease_spec_targets_scanning_status():
    assert SKILL_SCAN_JOB_LEASE.document is SkillScanJob
    assert SKILL_SCAN_JOB_LEASE.leased_statuses == frozenset({"scanning"})
    assert skill_scan_job_repository.spec is SKILL_SCAN_JOB_LEASE


def test_claimable_filter_shape():
    now = datetime.now(UTC)
    filt = claimable_skill_scan_job_filter(now)
    attempt_clause, status_clause = filt["$and"]

    # under the attempt cap (or missing attemptCount)
    assert {"attemptCount": {"$lt": SKILL_SCAN_JOB_MAX_ATTEMPTS}} in attempt_clause["$or"]
    assert {"attemptCount": {"$exists": False}} in attempt_clause["$or"]

    # runnable = PENDING (no/expired lease) OR SCANNING with an expired lease
    pending_clause, scanning_clause = status_clause["$or"]
    assert pending_clause["status"] == "pending"
    assert {"leaseExpiresAt": {"$lte": now}} in pending_clause["$or"]
    assert scanning_clause == {"status": "scanning", "leaseExpiresAt": {"$lte": now}}
