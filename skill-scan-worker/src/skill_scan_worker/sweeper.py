"""Two sweeps: the exhausted-job reaper (runner 0's before_poll) and the daily orphan sweep (leader-leased)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId

from registry_pkgs.database.leased_job import Lease, LeasedJobRunner, LeasedRepository
from registry_pkgs.database.skill_scan_job_repository import SKILL_SCAN_JOB_MAX_ATTEMPTS, skill_scan_job_repository
from registry_pkgs.models.enums import SkillScanJobStatus
from registry_pkgs.models.extended_skill import ExtendedSkill
from registry_pkgs.models.leader_lease import (
    LEADER_LEASE,
    LeaderLease,
    ensure_leader_document,
    leader_claimable_filter,
    release_leader,
)
from registry_pkgs.models.skill_scan_job import SkillScanJob
from registry_pkgs.models.skill_scan_result import SkillScanResult
from registry_pkgs.skill_scan.plan import delete_skill_scan_state

logger = logging.getLogger(__name__)

_ORPHAN_LEASE_NAME = "skill-scan-orphan-sweep"
_SWEEP_BATCH = 500
# Poll faster than the (daily) pass interval so a just-released lease is re-claimed promptly once due.
_ORPHAN_POLL_INTERVAL = timedelta(minutes=5)


async def fail_exhausted_jobs() -> None:
    """Finalize scanning jobs whose lease expired at the attempt cap; re-requested ones get fresh attempts.

    ``reap_one`` only reaps scanning jobs with an expired lease; its filter includes ``rerequested`` so an
    enqueue racing the reaper lands either before it (requeued by loop 1) or after it (on a pending/failed job,
    which ``enqueue_skill_scan`` resets).
    """
    now = datetime.now(UTC)

    # Loop 1: a request that arrived during the crashed last attempt gets fresh attempts, not a drop.
    while (
        await skill_scan_job_repository.reap_one(
            extra_filter={"attemptCount": {"$gte": SKILL_SCAN_JOB_MAX_ATTEMPTS}, "rerequested": True},
            set_fields={
                "status": SkillScanJobStatus.PENDING.value,
                "attemptCount": 0,
                "rerequested": False,
                "errorCode": None,
                "error": None,
            },
        )
        is not None
    ):
        pass

    # Loop 2: fail the rest.
    while (
        await skill_scan_job_repository.reap_one(
            extra_filter={"attemptCount": {"$gte": SKILL_SCAN_JOB_MAX_ATTEMPTS}, "rerequested": {"$ne": True}},
            set_fields={
                "status": SkillScanJobStatus.FAILED.value,
                "errorCode": "attempts_exhausted",
                "finishedAt": now,
            },
        )
        is not None
    ):
        pass


class OrphanSweeper:
    """Leader-leased daily sweep deleting scan state for skills Chat deleted without going through Registry."""

    def __init__(self, owner: str, interval_seconds: float) -> None:
        self._interval = timedelta(seconds=interval_seconds)
        self._repo: LeasedRepository[LeaderLease] = LeasedRepository(LEADER_LEASE)
        self._runner: LeasedJobRunner[LeaderLease] = LeasedJobRunner(
            name=_ORPHAN_LEASE_NAME,
            claim=self._claim,
            execute=self._execute,
            repository=self._repo,
            poll_interval=_ORPHAN_POLL_INTERVAL,
            owner=owner,
        )

    async def start(self) -> None:
        await ensure_leader_document(_ORPHAN_LEASE_NAME)
        await self._runner.start()

    async def shutdown(self) -> None:
        await self._runner.shutdown()

    async def _claim(self, owner: str) -> tuple[LeaderLease, Lease] | None:
        return await self._repo.claim(
            owner=owner, claimable_filter=leader_claimable_filter(_ORPHAN_LEASE_NAME, datetime.now(UTC))
        )

    async def _execute(self, _lease_doc: LeaderLease, lease: Lease) -> None:
        try:
            await self._sweep()
        finally:
            await release_leader(self._repo, lease, interval=self._interval)

    async def _sweep(self) -> None:
        skill_ids: set[PydanticObjectId] = set(await SkillScanJob.get_pymongo_collection().distinct("skillId"))
        skill_ids |= set(await SkillScanResult.get_pymongo_collection().distinct("skillId"))
        ids = list(skill_ids)
        skills = ExtendedSkill.get_pymongo_collection()
        deleted = 0
        for start in range(0, len(ids), _SWEEP_BATCH):
            batch = ids[start : start + _SWEEP_BATCH]
            existing = {doc["_id"] async for doc in skills.find({"_id": {"$in": batch}}, projection={"_id": 1})}
            for skill_id in batch:
                if skill_id not in existing:
                    await delete_skill_scan_state(skill_id, session=None)
                    deleted += 1
            await asyncio.sleep(0)
        if deleted:
            logger.info("Orphan sweep deleted scan state for %d skill(s) no longer present", deleted)
