"""Read skill scan state and request manual scans; business logic for the scan API routes.

Registry only queues scans and reads their results — it never runs a scan itself. The write paths
enqueue inside their transaction (see skill_service / skill_sync_apply_service); this service serves
the results the scanner worker writes back, and queues MANUAL scans.
"""

import logging

from beanie import PydanticObjectId
from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile
from registry_pkgs.models import SkillScanJob, SkillScanResult
from registry_pkgs.models.enums import SkillScanJobStatus, SkillScanTriggerType
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.skill_scan.content import compute_content_digest
from registry_pkgs.skill_scan.enqueue import enqueue_skill_scan
from registry_pkgs.skill_scan.plan import (
    current_tool_fingerprints,
    delete_skill_scan_state,
    tools_needing_scan,
)

from ..schemas.skill_api_schemas import (
    SkillScanBriefResponse,
    SkillScanFindingResponse,
    SkillScanJobResponse,
    SkillScanResponse,
    SkillScanStatus,
    SkillScanSummaryResponse,
    SkillScanToolResultResponse,
)
from .access_control_service import ACLService

logger = logging.getLogger(__name__)


def _require_user_id(user_id: str | None) -> PydanticObjectId:
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user ID is required")
    try:
        return PydanticObjectId(user_id)
    except Exception as e:  # noqa: BLE001 — any malformed id is an unauthenticated request
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user ID") from e


def _scan_status(job: SkillScanJob | None) -> SkillScanStatus:
    """Map the job's status onto the API-facing scanStatus; no job -> not_scanned, done -> completed."""
    if job is None:
        return "not_scanned"
    if job.status == SkillScanJobStatus.DONE:
        return "completed"
    return job.status  # pending / scanning / failed share their value with the API literal


def _result_outdated(tool: str, fingerprint: str, fingerprints: dict[str, str] | None) -> bool:
    """A result is outdated when its tool is gone from the published toolset or its fingerprint changed.

    False for every result when no toolset is published (nothing to compare against).
    """
    if fingerprints is None:
        return False
    current = fingerprints.get(tool)
    return current is None or current != fingerprint


class SkillScanService:
    def __init__(self, acl_service: ACLService) -> None:
        self._acl_service = acl_service

    async def get_scan(self, skill_id: PydanticObjectId, user_id: str | None) -> SkillScanResponse:
        skill = await self._require_skill(skill_id)
        await self._acl_service.check_user_permission(
            user_id=_require_user_id(user_id),
            resource_type=RegistryResourceType.SKILL.value,
            resource_id=skill_id,
            required_permission="VIEW",
        )
        return await self._scan_response(skill)

    async def request_scan(self, skill_id: PydanticObjectId, user_id: str | None) -> tuple[SkillScanResponse, bool]:
        """Returns (response, queued). queued False -> route answers 200, True -> 202."""
        skill = await self._require_skill(skill_id)
        await self._acl_service.check_user_permission(
            user_id=_require_user_id(user_id),
            resource_type=RegistryResourceType.SKILL.value,
            resource_id=skill_id,
            required_permission="EDIT",
        )
        files = await SkillFile.find({"skillId": skill_id}).to_list()
        digest = compute_content_digest(skill, files)
        fingerprints = await current_tool_fingerprints()
        # When a toolset IS published and every enabled tool already has a succeeded/skipped result
        # for the current digest, there is nothing to run. When none is published, always queue so
        # the scan runs once a scanner is deployed.
        if fingerprints is not None and not await tools_needing_scan(skill_id, digest, fingerprints):
            return await self._scan_response(skill), False
        try:
            await enqueue_skill_scan(
                skill_id,
                trigger=SkillScanTriggerType.MANUAL,
                requested_by=str(user_id),
                session=None,
            )
        except DuplicateKeyError:
            # A concurrent request created the job first (skillId is unique) — that is the
            # queued outcome we wanted, so treat it as success rather than a 500.
            pass
        if await Skill.find_one({"_id": skill_id}) is None:
            # A delete_skill transaction committed between our checks and this non-transactional
            # upsert; the upsert may have resurrected an orphan job for the now-deleted skill, so
            # drop the scan state again and report the skill as gone rather than leave the orphan.
            await delete_skill_scan_state(skill_id, session=None)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        return await self._scan_response(skill), True

    async def _scan_response(self, skill: Skill) -> SkillScanResponse:
        """Build the scan response for an already-fetched, already-authorized skill."""
        job = await SkillScanJob.find_one({"skillId": skill.id})
        fingerprints = await current_tool_fingerprints()
        return await self._build_response(skill, job, fingerprints)

    async def summaries_for(self, skills: list[Skill]) -> dict[PydanticObjectId, SkillScanBriefResponse]:
        """One skill_scan_jobs query plus one current_tool_fingerprints() for the whole page."""
        ids = [skill.id for skill in skills if skill.id is not None]
        if not ids:
            return {}
        jobs = await SkillScanJob.find({"skillId": {"$in": ids}}).to_list()
        fingerprints = await current_tool_fingerprints()
        jobs_by_skill = {job.skillId: job for job in jobs}
        versions = {skill.id: skill.version for skill in skills if skill.id is not None}
        briefs: dict[PydanticObjectId, SkillScanBriefResponse] = {}
        for skill_id in ids:
            briefs[skill_id] = self._brief(jobs_by_skill.get(skill_id), versions.get(skill_id, 0), fingerprints)
        return briefs

    @staticmethod
    def _brief(
        job: SkillScanJob | None, skill_version: int, fingerprints: dict[str, str] | None
    ) -> SkillScanBriefResponse:
        if job is None or job.summary is None:
            return SkillScanBriefResponse(scanStatus=_scan_status(job))
        summary = job.summary
        return SkillScanBriefResponse(
            scanStatus=_scan_status(job),
            status=summary.status,
            maxSeverity=summary.maxSeverity,
            severityCounts=summary.severityCounts,
            stale=summary.skillVersion != skill_version,
            outdatedTool=any(_result_outdated(t.tool, t.toolFingerprint, fingerprints) for t in summary.tools),
            scannedAt=summary.scannedAt,
        )

    async def _build_response(
        self, skill: Skill, job: SkillScanJob | None, fingerprints: dict[str, str] | None
    ) -> SkillScanResponse:
        summary = job.summary if job is not None else None
        results: list[SkillScanToolResultResponse] = []
        if summary is not None:
            # Scoped to the summary's fingerprints: a rescan after a scanner/model change writes new
            # rows (the unique key is skillId+contentDigest+toolFingerprint) without replacing the old
            # ones, so an unscoped query would return stale duplicates and spurious outdatedTool.
            raw = await SkillScanResult.find(
                {
                    "skillId": skill.id,
                    "contentDigest": summary.contentDigest,
                    "toolFingerprint": {"$in": [t.toolFingerprint for t in summary.tools]},
                }
            ).to_list()
            raw.sort(key=lambda r: r.tool)
            results = [self._tool_result(r, fingerprints) for r in raw]
        return SkillScanResponse(
            skillId=str(skill.id),
            skillVersion=skill.version,
            scanStatus=_scan_status(job),
            stale=summary is not None and summary.skillVersion != skill.version,
            outdatedTool=any(r.outdatedTool for r in results),
            job=self._job_response(job) if job is not None else None,
            summary=self._summary_response(summary) if summary is not None else None,
            results=results,
        )

    @staticmethod
    def _job_response(job: SkillScanJob) -> SkillScanJobResponse:
        return SkillScanJobResponse(
            status=job.status,
            rerequested=job.rerequested,
            triggerType=job.triggerType,
            requestedAt=job.requestedAt,
            startedAt=job.startedAt,
            finishedAt=job.finishedAt,
            attemptCount=job.attemptCount,
            errorCode=job.errorCode,
        )

    @staticmethod
    def _summary_response(summary) -> SkillScanSummaryResponse:  # noqa: ANN001 — SkillScanSummary
        return SkillScanSummaryResponse(
            contentDigest=summary.contentDigest,
            skillVersion=summary.skillVersion,
            status=summary.status,
            maxSeverity=summary.maxSeverity,
            severityCounts=summary.severityCounts,
            scannedAt=summary.scannedAt,
        )

    @staticmethod
    def _tool_result(result: SkillScanResult, fingerprints: dict[str, str] | None) -> SkillScanToolResultResponse:
        return SkillScanToolResultResponse(
            tool=result.tool,
            toolVersion=result.toolVersion,
            status=result.status,
            statusReason=result.statusReason,
            outdatedTool=_result_outdated(result.tool, result.toolFingerprint, fingerprints),
            findings=[SkillScanFindingResponse(**f.model_dump()) for f in result.findings],
            findingsTruncated=result.findingsTruncated,
            llmProviderType=result.llm.providerType if result.llm else None,
            llmModelId=result.llm.modelId if result.llm else None,
            inputTokens=result.usage.inputTokens if result.usage else None,
            outputTokens=result.usage.outputTokens if result.usage else None,
            durationMs=result.durationMs,
            errorMessage=result.errorMessage,
            toolMetadata=result.toolMetadata,
            scannedAt=result.updatedAt,
        )

    @staticmethod
    async def _require_skill(skill_id: PydanticObjectId) -> Skill:
        skill = await Skill.find_one({"_id": skill_id})
        if skill is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        return skill
