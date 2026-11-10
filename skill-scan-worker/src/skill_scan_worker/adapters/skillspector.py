"""NVIDIA SkillSpector adapter, pinned to v2.13.3.

Sends all skill content to the LLM in chunks (``llm_input_unbounded=True``); the worker applies the size
cap, not the tool. Exit 0 and 1 are both normal (1 = risk score above 50); exit 2 is an error.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from registry_pkgs.models.enums import (
    ModelSourceProviderType,
    SkillScanResultStatus,
    SkillScanStatusReason,
)
from registry_pkgs.models.skill_scan_result import ScanFinding, ScanUsage
from skill_scan_worker.adapters.base import ToolOutcome, static_fingerprint_for
from skill_scan_worker.adapters.categories import (
    coerce_int,
    map_skillspector_category,
    parse_confidence,
    parse_severity,
)
from skill_scan_worker.config import settings
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import Invocation, ProcessResult

logger = logging.getLogger(__name__)

_MAX_LLM_CONCURRENCY = 2  # cap concurrent LLM calls per scan
_MAX_WORKFLOW_SECONDS = 540  # below the worker's 600s tool timeout so it writes a report before being killed
# v2.13.3 exits 0 or 1 after writing a full report (1: incomplete, findings or high risk; cli.py:796-810) and 2 on
# error. Anything else, such as a signal kill, is an error even if a report file exists.
_REPORT_EXIT_CODES = frozenset({0, 1})
# Token counter keys in each metadata.inference_usage[] record (src/skillspector/inference_usage.py).
_PROMPT_TOKENS = "prompt_tokens"
_COMPLETION_TOKENS = "completion_tokens"

# Bedrock only: v2.13.3's default structured output is a forced tool call, which Claude Sonnet 5.5 rejects
# ('tool_choice: type "tool" and "any" are not supported for this model'), failing every LLM batch. json_schema
# avoids the forced tool call. (v2.13.3 sends no temperature unless SKILLSPECTOR_TEMPERATURE is set.)
_LLM_BEDROCK_ENV = {"SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD": "json_schema"}

_STATIC_CONFIG = {
    "format": "json",
    "flags": [],
    "max_llm_concurrency": _MAX_LLM_CONCURRENCY,
    "max_workflow_seconds": _MAX_WORKFLOW_SECONDS,
    "llm_env": {"bedrock": _LLM_BEDROCK_ENV},
}


class SkillSpectorAdapter:
    name = "skillspector"
    tool_version = "2.13.3"
    llm_input_unbounded = True  # sends every file to the LLM in chunks; the worker applies the size limit

    def static_fingerprint(self) -> str:
        return static_fingerprint_for(self.name, self.tool_version, _STATIC_CONFIG)

    def build_invocation(self, skill_dir: Path, output_file: Path, llm: ResolvedLLM | None) -> Invocation:
        binary = str(Path(settings.tools_dir) / "skillspector" / "bin" / "skillspector")
        argv = [binary, "scan", str(skill_dir), "-f", "json", "-o", str(output_file)]
        env: dict[str, str] = {
            "SKILLSPECTOR_MAX_LLM_CONCURRENCY": str(_MAX_LLM_CONCURRENCY),
            "SKILLSPECTOR_MAX_WORKFLOW_SECONDS": str(_MAX_WORKFLOW_SECONDS),
            "SKILLSPECTOR_LOG_LEVEL": "WARNING",
        }
        secrets: list[str] = []

        llm_env = _llm_env(llm, secrets) if llm is not None else None
        if llm_env is None:
            # Never let SkillSpector fall back to its default nv_build (NVIDIA cloud) provider.
            argv.append("--no-llm")
        else:
            env.update(llm_env)

        return Invocation(
            argv=argv,
            output_file=output_file,
            env=env,
            secrets=tuple(secrets),
            llm_used=llm_env is not None,
        )

    def parse(self, process: ProcessResult, *, llm_requested: bool) -> ToolOutcome:
        if process.exit_code not in _REPORT_EXIT_CODES:
            return _failed(SkillScanStatusReason.TOOL_ERROR, process.stderr_tail)
        try:
            raw = process.output_file.read_bytes()
        except OSError:
            return _failed(SkillScanStatusReason.TOOL_ERROR, process.stderr_tail)
        if not raw.strip():
            return _failed(SkillScanStatusReason.TOOL_ERROR, process.stderr_tail)
        try:
            report = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _failed(SkillScanStatusReason.PARSE_ERROR, process.stderr_tail)
        # v2.13.3 always writes the issues array (nodes/report.py); a report without one is not a scan result.
        if not isinstance(report, dict) or not isinstance(report.get("issues"), list):
            return _failed(SkillScanStatusReason.PARSE_ERROR, process.stderr_tail)
        if report.get("execution_successful") is False:
            return _failed(SkillScanStatusReason.TOOL_ERROR, process.stderr_tail)

        metadata = report.get("metadata") or {}
        completeness = report.get("analysis_completeness") or {}
        status, reason = _status(
            llm_requested=llm_requested,
            llm_available=metadata.get("llm_available"),
            llm_degraded=metadata.get("llm_degraded"),
            is_complete=completeness.get("is_complete"),
        )

        findings = _dedupe([_finding(i) for i in report["issues"] if isinstance(i, dict)])
        risk = report.get("risk_assessment") or {}
        return ToolOutcome(
            status=status,
            status_reason=reason,
            findings=findings,
            usage=_usage(metadata),
            tool_metadata={
                "riskScore": risk.get("score"),
                "riskSeverity": risk.get("severity"),
                "recommendation": risk.get("recommendation"),
                "coveragePercent": completeness.get("coverage_percent"),
            },
        )


def _llm_env(llm: ResolvedLLM, secrets: list[str]) -> dict[str, str] | None:
    """Env for SkillSpector's LLM pass, or ``None`` when this ``ResolvedLLM`` is not usable."""
    if llm.provider == ModelSourceProviderType.AWS_BEDROCK:
        if not llm.bedrock_model or not llm.aws_region:
            return None
        # v2.13.3 assumes an ARN is an Anthropic model; a non-Anthropic ARN would be routed wrong.
        if llm.bedrock_model.startswith("arn:") and "anthropic" not in llm.base_model_id.lower():
            return None
        return {
            **_LLM_BEDROCK_ENV,
            "SKILLSPECTOR_PROVIDER": "bedrock",
            "SKILLSPECTOR_MODEL": llm.bedrock_model,
            "AWS_REGION": llm.aws_region,
        }
    if llm.provider == ModelSourceProviderType.AZURE_OPENAI:
        # SkillSpector's azure_openai provider requires an API key; Workload Identity is unsupported.
        if llm.azure_api_key is None:
            return None
        if not llm.azure_endpoint or not llm.azure_deployment or not llm.azure_api_version:
            return None
        key = llm.azure_api_key.get_secret_value()
        secrets.append(key)
        return {
            "SKILLSPECTOR_PROVIDER": "azure_openai",
            "AZURE_OPENAI_API_KEY": key,
            "AZURE_OPENAI_ENDPOINT": llm.azure_endpoint,
            "AZURE_OPENAI_DEPLOYMENT": llm.azure_deployment,
            "AZURE_OPENAI_API_VERSION": llm.azure_api_version,
            "SKILLSPECTOR_MODEL": llm.base_model_id,  # for its context-length registry lookup
        }
    return None


def _status(
    *, llm_requested: bool, llm_available: object, llm_degraded: object, is_complete: object
) -> tuple[SkillScanResultStatus, SkillScanStatusReason | None]:
    if not llm_requested:
        return SkillScanResultStatus.PARTIAL, SkillScanStatusReason.LLM_UNAVAILABLE
    # llm_available covers only preflight and the meta_analyzer call. Failed semantic-analyzer batches leave it
    # true and set llm_degraded instead, without touching analysis_completeness (nodes/report.py at v2.13.3).
    if llm_available is False or llm_degraded is True:
        return SkillScanResultStatus.PARTIAL, SkillScanStatusReason.LLM_FAILED
    if is_complete is False:
        return SkillScanResultStatus.PARTIAL, SkillScanStatusReason.ANALYSIS_INCOMPLETE
    return SkillScanResultStatus.SUCCEEDED, None


def _finding(entry: dict[str, Any]) -> ScanFinding:
    location = entry.get("location") or {}
    return ScanFinding(
        tool=SkillSpectorAdapter.name,
        ruleId=entry.get("id"),
        severity=parse_severity(entry.get("severity"), tool=SkillSpectorAdapter.name),
        toolCategory=entry.get("category"),
        category=map_skillspector_category(entry.get("category")),
        title=entry.get("pattern") or entry.get("category") or "",
        message=entry.get("explanation") or entry.get("finding") or "",
        filePath=location.get("file"),
        startLine=coerce_int(location.get("start_line")),
        endLine=coerce_int(location.get("end_line")),
        snippet=entry.get("code_snippet") or entry.get("finding"),
        remediation=entry.get("remediation"),
        confidence=parse_confidence(entry.get("confidence")),
    )


def _dedupe(findings: list[ScanFinding]) -> list[ScanFinding]:
    """Collapse issues that share (ruleId, filePath, startLine). SkillSpector repeats a rule for overlapping
    matches on one line. This is within-tool only, not cross-tool deduplication."""
    seen: set[tuple[str | None, str | None, int | None]] = set()
    out: list[ScanFinding] = []
    for finding in findings:
        key = (finding.ruleId, finding.filePath, finding.startLine)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def _usage(metadata: dict[str, Any]) -> ScanUsage | None:
    """Sum ``prompt_tokens``/``completion_tokens`` over ``metadata.inference_usage[]`` (the counter names
    ``sanitize_inference_usage`` emits at v2.13.3). ``None`` when no entry carries either counter: SkillSpector
    documents an empty list as "the provider supplied no counters", not as zero cost."""
    entries = metadata.get("inference_usage")
    if not isinstance(entries, list):
        return None
    inp = 0
    out = 0
    seen = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prompt = coerce_int(entry.get(_PROMPT_TOKENS))
        completion = coerce_int(entry.get(_COMPLETION_TOKENS))
        if prompt is None and completion is None:
            continue
        seen = True
        inp += max(prompt or 0, 0)
        out += max(completion or 0, 0)
    if not seen:
        return None
    return ScanUsage(inputTokens=inp, outputTokens=out)


def _failed(reason: SkillScanStatusReason, stderr_tail: str) -> ToolOutcome:
    return ToolOutcome(
        status=SkillScanResultStatus.FAILED,
        status_reason=reason,
        findings=[],
        error_message=stderr_tail or None,
    )
