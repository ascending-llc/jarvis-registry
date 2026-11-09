"""Execute one claimed scan job: plan, materialize, run each adapter, then finish (change 4-5)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from beanie import PydanticObjectId

from registry_pkgs.database.leased_job import Lease
from registry_pkgs.database.skill_scan_job_repository import skill_scan_job_repository
from registry_pkgs.models.enums import (
    SCAN_SEVERITY_RANK,
    ScanSeverity,
    SkillScanJobStatus,
    SkillScanResultStatus,
    SkillScanStatusReason,
)
from registry_pkgs.models.extended_skill import ExtendedSkill
from registry_pkgs.models.extended_skill_file import ExtendedSkillFile
from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.models.skill_scan_job import SkillScanJob
from registry_pkgs.models.skill_scan_result import (
    MAX_FINDINGS_PER_RESULT,
    ScanFinding,
    ScanLLMInfo,
    SkillScanResult,
)
from registry_pkgs.skill_scan.content import (
    SkillNotScannableError,
    compute_content_digest,
    has_unreadable_files,
    materialize_skill,
    render_skill_md,
)
from registry_pkgs.skill_scan.plan import (
    compose_tool_fingerprint,
    delete_skill_scan_state,
    resolve_skill_scan_model_source,
    scan_llm_info,
    tools_needing_scan,
)
from registry_pkgs.telemetry.skill_scan_metrics import record_llm_tokens, record_tool_duration, record_tool_run
from skill_scan_worker.adapters import ENABLED_ADAPTERS
from skill_scan_worker.adapters.base import ScannerAdapter, ToolOutcome
from skill_scan_worker.config import settings
from skill_scan_worker.finish import finish_job
from skill_scan_worker.llm import ResolvedLLM, resolve_llm
from skill_scan_worker.process import ProcessResult, run_tool_process

logger = logging.getLogger(__name__)

_CANCEL_REQUEUE_TIMEOUT = 5.0


async def execute_job(job: SkillScanJob, lease: Lease) -> None:
    """Run the full scan for one claimed job, or requeue it on shutdown."""
    try:
        await _scan(job, lease)
    except asyncio.CancelledError:
        # Shutdown, at any point of the job (including the initial loads): process.py has killed any running
        # tool. Restore the attempt this claim consumed so a rollout does not burn one of the three attempts;
        # shielded and bounded so it can't hang shutdown.
        with suppress(Exception):
            await asyncio.wait_for(
                asyncio.shield(
                    skill_scan_job_repository.transition(
                        lease,
                        {"status": SkillScanJobStatus.PENDING.value, "attemptCount": job.attemptCount - 1},
                        release=True,
                    )
                ),
                timeout=_CANCEL_REQUEUE_TIMEOUT,
            )
        raise


async def _scan(job: SkillScanJob, lease: Lease) -> None:
    skill = await ExtendedSkill.get(job.skillId)
    if skill is None:
        # Deleted in Chat (Chat bypasses Registry): delete_skill_scan_state removes the job and results.
        await delete_skill_scan_state(job.skillId, session=None)
        return
    assert skill.id is not None  # a fetched Document always has an id; narrows PydanticObjectId | None

    files = await ExtendedSkillFile.find(ExtendedSkillFile.skillId == skill.id).to_list()
    digest = compute_content_digest(skill, files)
    source = await resolve_skill_scan_model_source()
    llm_info = scan_llm_info(source)
    fingerprints = {a.name: compose_tool_fingerprint(a.static_fingerprint(), llm_info) for a in ENABLED_ADAPTERS}
    adapters_by_name = {a.name: a for a in ENABLED_ADAPTERS}

    pending = await tools_needing_scan(skill.id, digest, fingerprints)

    results: list[SkillScanResult] = []
    if pending:
        if has_unreadable_files(skill, files):
            results = [
                _no_process_result(
                    skill,
                    digest,
                    adapters_by_name[name],
                    fingerprints[name],
                    llm_info,
                    status=SkillScanResultStatus.SKIPPED,
                    status_reason=SkillScanStatusReason.CHAT_SUPPORTING_FILES_UNSUPPORTED,
                )
                for name in pending
            ]
        else:
            results = await _run_pending(
                skill, files, digest, source, llm_info, fingerprints, pending, adapters_by_name
            )
    await finish_job(lease, skill, digest, fingerprints, results)


async def _run_pending(
    skill: ExtendedSkill,
    files: list[ExtendedSkillFile],
    digest: str,
    source: ModelSource | None,
    llm_info: ScanLLMInfo | None,
    fingerprints: dict[str, str],
    pending: list[str],
    adapters_by_name: dict[str, ScannerAdapter],
) -> list[SkillScanResult]:
    assert skill.id is not None  # narrowed by execute_job's check; re-narrow for this scope
    run_dir = Path(tempfile.mkdtemp(dir=settings.work_dir))
    try:
        skill_dir = run_dir / "skill"
        try:
            # Synchronous file I/O: keep it off the event loop so it never starves the lease heartbeat.
            await asyncio.to_thread(materialize_skill, skill, files, skill_dir)
        except SkillNotScannableError as e:
            # Content Registry cannot read safely (e.g. clashing file paths): a tool failure, not retryable.
            return [
                _no_process_result(
                    skill,
                    digest,
                    adapters_by_name[name],
                    fingerprints[name],
                    llm_info,
                    status=SkillScanResultStatus.FAILED,
                    status_reason=SkillScanStatusReason.TOOL_ERROR,
                    error_message=str(e),
                )
                for name in pending
            ]

        resolved = await resolve_llm(source)
        text_bytes = len(render_skill_md(skill).encode("utf-8")) + sum(f.bytes for f in files if not f.isBinary)

        results: list[SkillScanResult] = []
        for adapter in ENABLED_ADAPTERS:
            if adapter.name not in pending:
                continue
            size_skipped = (
                resolved is not None and adapter.llm_input_unbounded and text_bytes > settings.llm_max_text_bytes
            )
            llm = None if size_skipped else resolved
            results.append(
                await run_adapter(
                    adapter,
                    skill_dir,
                    run_dir,
                    llm,
                    size_skipped,
                    fingerprints[adapter.name],
                    llm_info,
                    skill_id=skill.id,
                    skill_version=skill.version,
                    content_digest=digest,
                )
            )
        return results
    finally:
        await asyncio.to_thread(shutil.rmtree, run_dir, ignore_errors=True)


async def run_adapter(
    adapter: ScannerAdapter,
    skill_dir: Path,
    run_dir: Path,
    llm: ResolvedLLM | None,
    size_skipped: bool,
    fingerprint: str,
    llm_info: ScanLLMInfo | None,
    *,
    skill_id: PydanticObjectId,
    skill_version: int,
    content_digest: str,
) -> SkillScanResult:
    """Run one adapter's tool and normalize its outcome into a stored result."""
    output_file = run_dir / f"{adapter.name}.out"
    invocation = adapter.build_invocation(skill_dir, output_file, llm)
    process = await run_tool_process(
        invocation, cwd=run_dir, timeout=settings.tool_timeout_seconds, max_output_bytes=settings.max_output_bytes
    )

    if process.timed_out:
        outcome = ToolOutcome(
            SkillScanResultStatus.FAILED,
            SkillScanStatusReason.TIMEOUT,
            [],
            error_message=f"tool timed out after {settings.tool_timeout_seconds}s",
        )
    elif process.output_too_large:
        outcome = ToolOutcome(
            SkillScanResultStatus.FAILED,
            SkillScanStatusReason.PARSE_ERROR,
            [],
            error_message="tool output exceeded the size limit",
        )
    else:
        try:
            # parse reads up to max_output_bytes from disk and may be CPU-heavy; keep it off the event
            # loop so it never starves the lease heartbeat.
            outcome = await asyncio.to_thread(adapter.parse, process, llm_requested=invocation.llm_used)
        except Exception as e:
            outcome = ToolOutcome(
                SkillScanResultStatus.FAILED,
                SkillScanStatusReason.PARSE_ERROR,
                [],
                error_message=f"parse error: {type(e).__name__}: {e}",
            )

    if size_skipped and outcome.status in (SkillScanResultStatus.SUCCEEDED, SkillScanResultStatus.PARTIAL):
        outcome = replace(
            outcome, status=SkillScanResultStatus.PARTIAL, status_reason=SkillScanStatusReason.LLM_SKIPPED_SIZE_LIMIT
        )

    findings = _normalize_findings(outcome.findings, skill_dir)
    findings.sort(key=lambda f: SCAN_SEVERITY_RANK[ScanSeverity(f.severity)], reverse=True)
    truncated = len(findings) > MAX_FINDINGS_PER_RESULT
    findings = findings[:MAX_FINDINGS_PER_RESULT]

    result = SkillScanResult(
        skillId=skill_id,
        contentDigest=content_digest,
        skillVersion=skill_version,
        tool=adapter.name,
        toolVersion=adapter.tool_version,
        toolFingerprint=fingerprint,
        status=outcome.status,
        statusReason=outcome.status_reason,
        findings=findings,
        findingsTruncated=truncated,
        llm=llm_info,  # the configured model, even when this run used none (change 9)
        usage=outcome.usage,
        toolMetadata=outcome.tool_metadata,
        errorMessage=_redact(outcome.error_message, invocation.secrets),
        durationMs=process.duration_ms,
    )
    _record_metrics(adapter.name, outcome, process)
    return result


def _normalize_findings(findings: list[ScanFinding], skill_dir: Path) -> list[ScanFinding]:
    """Rewrite absolute finding paths under the skill dir to be relative, so no temp path is ever stored."""
    root = skill_dir.resolve()
    out: list[ScanFinding] = []
    for finding in findings:
        path = finding.filePath
        if path:
            candidate = Path(path)
            if candidate.is_absolute():
                with suppress(ValueError):
                    finding = finding.model_copy(update={"filePath": str(candidate.resolve().relative_to(root))})
        out.append(finding)
    return out


def _redact(message: str | None, secrets: tuple[str, ...]) -> str | None:
    if message is None:
        return None
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message


def _record_metrics(tool: str, outcome: ToolOutcome, process: ProcessResult) -> None:
    record_tool_run(tool, str(outcome.status), str(outcome.status_reason) if outcome.status_reason else None)
    record_tool_duration(tool, process.duration_ms / 1000)
    if outcome.usage is not None:
        if outcome.usage.inputTokens:
            record_llm_tokens(tool, "input", outcome.usage.inputTokens)
        if outcome.usage.outputTokens:
            record_llm_tokens(tool, "output", outcome.usage.outputTokens)


def _no_process_result(
    skill: ExtendedSkill,
    digest: str,
    adapter: ScannerAdapter,
    fingerprint: str,
    llm_info: ScanLLMInfo | None,
    *,
    status: SkillScanResultStatus,
    status_reason: SkillScanStatusReason,
    error_message: str | None = None,
) -> SkillScanResult:
    """A result recorded without running any process (skipped unreadable files, or an unscannable skill)."""
    return SkillScanResult(
        skillId=skill.id,
        contentDigest=digest,
        skillVersion=skill.version,
        tool=adapter.name,
        toolVersion=adapter.tool_version,
        toolFingerprint=fingerprint,
        status=status,
        statusReason=status_reason,
        findings=[],
        llm=llm_info,
        errorMessage=error_message,
    )
