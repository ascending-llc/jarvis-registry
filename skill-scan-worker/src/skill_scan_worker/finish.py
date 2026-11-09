"""Summary computation and the fenced, single-transaction finish (change 6).

Results, summary and job state become visible together, or not at all: the final fenced transition is the
atomicity gate. If the lease was lost or the skill was deleted mid-scan (Registry deleted the job, so the fence
misses), the transition matches nothing, ``LeaseLostError`` is raised, the transaction aborts and every result
from this run is discarded.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.database.leased_job import Lease, LeaseLostError
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.enums import SCAN_SEVERITY_RANK, ScanSeverity, SkillScanJobStatus, SkillScanResultStatus
from registry_pkgs.models.extended_skill import ExtendedSkill
from registry_pkgs.models.skill_scan_job import SkillScanSummary, SkillScanToolSummary
from registry_pkgs.models.skill_scan_result import SkillScanResult
from registry_pkgs.telemetry.skill_scan_metrics import record_job_finished
from skill_scan_worker.adapters import ENABLED_ADAPTERS
from skill_scan_worker.state import settle_or_requeue

logger = logging.getLogger(__name__)


async def finish_job(
    lease: Lease,
    skill: ExtendedSkill,
    content_digest: str,
    fingerprints: dict[str, str],
    results: list[SkillScanResult],
) -> None:
    """Persist results, compute the summary and transition the job — all in one transaction."""
    assert skill.id is not None  # a persisted skill always has an id; narrows PydanticObjectId | None
    skill_id = skill.id  # bind the narrowed id so the nested transaction closure keeps the non-None type
    now = datetime.now(UTC)
    client = MongoDB.get_client()

    async def _txn(session: AsyncClientSession) -> str:
        await _write_results(session, skill_id, content_digest, fingerprints, results)
        summary = await _build_summary(session, skill, content_digest, fingerprints, now)
        summary_doc = summary.model_dump(mode="python")

        result = await settle_or_requeue(
            lease,
            terminal_fields={"status": SkillScanJobStatus.DONE.value, "summary": summary_doc, "finishedAt": now},
            requeue_fields={
                "status": SkillScanJobStatus.PENDING.value,
                "summary": summary_doc,
                "finishedAt": now,
                "rerequested": False,
                "attemptCount": 0,
            },
            terminal_unset=["errorCode", "error"],
            requeue_unset=["errorCode", "error"],
            session=session,
        )
        if result == "lease_lost":
            # The lease was lost or the job was deleted. Abort the transaction, discarding all results.
            raise LeaseLostError(f"lost lease finishing skill scan job for skill {skill.id}")
        return "done" if result == "terminal" else "requeued"

    try:
        async with client.start_session() as session:
            outcome = await session.with_transaction(_txn)
    except LeaseLostError:
        record_job_finished("lease_lost")
        raise

    record_job_finished(outcome)


async def _write_results(
    session: AsyncClientSession,
    skill_id: PydanticObjectId,
    content_digest: str,
    fingerprints: dict[str, str],
    results: list[SkillScanResult],
) -> None:
    collection = SkillScanResult.get_pymongo_collection()
    for result in results:
        # exclude_none matches the model's keep_nulls=False: unset optional fields are omitted, not stored as null.
        doc = result.model_dump(mode="python", exclude={"id"}, exclude_none=True)
        await collection.replace_one(
            {"skillId": skill_id, "contentDigest": content_digest, "toolFingerprint": result.toolFingerprint},
            doc,
            upsert=True,
            session=session,
        )

    # Keep only the latest digest (D12).
    await collection.delete_many({"skillId": skill_id, "contentDigest": {"$ne": content_digest}}, session=session)

    # For each tool that produced a new result this run, drop its superseded old-fingerprint result. Tools that
    # did not run keep their old-fingerprint result, so it is still shown as outdatedTool (D15).
    for result in results:
        await collection.delete_many(
            {
                "skillId": skill_id,
                "contentDigest": content_digest,
                "tool": result.tool,
                "toolFingerprint": {"$ne": result.toolFingerprint},
            },
            session=session,
        )


async def _build_summary(
    session: AsyncClientSession,
    skill: ExtendedSkill,
    content_digest: str,
    fingerprints: dict[str, str],
    now: datetime,
) -> SkillScanSummary:
    collection = SkillScanResult.get_pymongo_collection()
    cursor = collection.find({"skillId": skill.id, "contentDigest": content_digest}, session=session)
    results = [SkillScanResult.model_validate(doc) async for doc in cursor]

    by_tool: dict[str, list[SkillScanResult]] = {}
    for result in results:
        by_tool.setdefault(result.tool, []).append(result)

    tool_summaries: list[SkillScanToolSummary] = []
    statuses: list[str] = []
    all_findings = []
    for adapter in ENABLED_ADAPTERS:
        tool_results = by_tool.get(adapter.name)
        if not tool_results:
            continue
        current = fingerprints.get(adapter.name)
        chosen = next((r for r in tool_results if r.toolFingerprint == current), None)
        if chosen is None:
            chosen = max(tool_results, key=lambda r: r.updatedAt)
        tool_summaries.append(
            SkillScanToolSummary(
                tool=adapter.name,
                toolFingerprint=chosen.toolFingerprint,
                status=chosen.status,
                statusReason=chosen.statusReason,
            )
        )
        statuses.append(str(chosen.status))
        all_findings.extend(chosen.findings)

    severity_counts: dict[ScanSeverity, int] = {}
    for finding in all_findings:
        sev = ScanSeverity(finding.severity)
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    max_severity = max(
        (ScanSeverity(f.severity) for f in all_findings), key=lambda s: SCAN_SEVERITY_RANK[s], default=None
    )

    return SkillScanSummary(
        contentDigest=content_digest,
        skillVersion=skill.version,
        status=_worst_status(statuses),
        maxSeverity=max_severity,
        severityCounts=severity_counts,
        tools=tool_summaries,
        scannedAt=now,
    )


def _worst_status(statuses: list[str]) -> SkillScanResultStatus:
    """Worst of failed > partial > succeeded; skipped only when every tool is skipped."""
    values = set(statuses)
    if SkillScanResultStatus.FAILED.value in values:
        return SkillScanResultStatus.FAILED
    if SkillScanResultStatus.PARTIAL.value in values:
        return SkillScanResultStatus.PARTIAL
    if values == {SkillScanResultStatus.SKIPPED.value}:
        return SkillScanResultStatus.SKIPPED
    return SkillScanResultStatus.SUCCEEDED
