"""Cisco ``cisco-ai-skill-scanner`` adapter, pinned to 2.3.2.

Behavioral + OSV analyzers, optional LLM pass. The tool never executes skill code. It is not run with
``--fail-on-*``, so a non-zero exit always means a runtime error, never "findings present".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from registry_pkgs.models.enums import ModelSourceProviderType, SkillScanResultStatus, SkillScanStatusReason
from registry_pkgs.models.skill_scan_result import ScanFinding, ScanUsage
from registry_pkgs.workflows.bedrock_ids import bedrock_litellm_model_id
from skill_scan_worker.adapters.base import ToolOutcome, static_fingerprint_for
from skill_scan_worker.adapters.categories import coerce_int, map_cisco_category, parse_confidence, parse_severity
from skill_scan_worker.config import settings
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import Invocation, ProcessResult

logger = logging.getLogger(__name__)

# Rule ids that are status signals, not security findings: dropped from the finding list but used for status.
_LLM_FAILED_RULE = "LLM_ANALYSIS_FAILED"
_LLM_BUDGET_RULE = "LLM_CONTEXT_BUDGET_EXCEEDED"
_STATUS_RULE_IDS = frozenset({_LLM_FAILED_RULE, _LLM_BUDGET_RULE})

# LLM request controls for every provider. 2.3.2 sends temperature=0.0 unless told otherwise, and newer models
# reject any temperature ("`temperature` is deprecated for this model": Claude Sonnet 5.x, Haiku 5.5). Its own
# model-name check that omits it can't see the model behind an application inference profile ARN or an Azure
# deployment name, so omit it always and let the provider default apply, as workflow models do.
_LLM_COMMON_ENV = {"SKILL_SCANNER_LLM_TEMPERATURE": "none"}
# Bedrock only: LiteLLM emulates a JSON schema there with a forced tool call, which Claude Sonnet 5.5 rejects
# ('tool_choice: type "tool" and "any" are not supported for this model'). Plain JSON with the schema in the
# prompt works for every Bedrock model. Same ARN blind spot as above.
_LLM_BEDROCK_ENV = {"SKILL_SCANNER_LLM_FORCE_JSON_OBJECT": "1"}

_STATIC_CONFIG = {
    "policy": "balanced",
    "analyzers": ["behavioral", "osv"],
    "llm_flags": [],
    "llm_env": {"common": _LLM_COMMON_ENV, "bedrock": _LLM_BEDROCK_ENV},
}


class CiscoSkillScannerAdapter:
    name = "cisco-skill-scanner"
    tool_version = "2.3.2"
    llm_input_unbounded = False  # Cisco caps LLM input itself (100k chars, default_policy.yaml)

    def static_fingerprint(self) -> str:
        return static_fingerprint_for(self.name, self.tool_version, _STATIC_CONFIG)

    def build_invocation(self, skill_dir: Path, output_file: Path, llm: ResolvedLLM | None) -> Invocation:
        binary = str(Path(settings.tools_dir) / "cisco" / "bin" / "skill-scanner")
        argv = [
            binary,
            "scan",
            str(skill_dir),
            "--format",
            "json",
            "--output-json",
            str(output_file),
            "--policy",
            "balanced",
            "--use-behavioral",
            "--use-osv",
        ]
        # Registry's own images set this so LiteLLM uses its bundled cost map instead of fetching one per run.
        env: dict[str, str] = {"LITELLM_LOCAL_MODEL_COST_MAP": "True"}
        secrets: list[str] = []

        llm_env = _llm_env(llm, secrets) if llm is not None else None
        if llm_env is not None:
            env.update(llm_env)
            argv.append("--use-llm")

        return Invocation(
            argv=argv,
            output_file=output_file,
            env=env,
            secrets=tuple(secrets),
            llm_used=llm_env is not None,
        )

    def parse(self, process: ProcessResult, *, llm_requested: bool) -> ToolOutcome:
        if process.exit_code != 0:
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
        # 2.3.2 always writes the findings array (ScanResult.to_dict); a report without one is not a scan result.
        if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
            return _failed(SkillScanStatusReason.PARSE_ERROR, process.stderr_tail)

        raw_findings = report["findings"]
        rule_ids = {f.get("rule_id") for f in raw_findings if isinstance(f, dict)}
        analyzers_failed = report.get("analyzers_failed") or []
        llm_analyzer_failed = any(
            "llm" in str(entry.get("analyzer", "")).lower() for entry in analyzers_failed if isinstance(entry, dict)
        )

        status, reason = _status(
            llm_requested=llm_requested,
            llm_failed=_LLM_FAILED_RULE in rule_ids or llm_analyzer_failed,
            budget_exceeded=_LLM_BUDGET_RULE in rule_ids,
            analyzers_failed=bool(analyzers_failed),
        )

        findings = [
            _finding(f) for f in raw_findings if isinstance(f, dict) and f.get("rule_id") not in _STATUS_RULE_IDS
        ]
        return ToolOutcome(
            status=status,
            status_reason=reason,
            findings=findings,
            usage=_usage(report),
            tool_metadata={
                "maxSeverity": report.get("max_severity"),
                "isSafe": report.get("is_safe"),
                "analyzersUsed": report.get("analyzers_used"),
            },
        )


def _llm_env(llm: ResolvedLLM, secrets: list[str]) -> dict[str, str] | None:
    """Env for Cisco's LLM pass, or ``None`` when this ``ResolvedLLM`` is not usable."""
    if llm.provider == ModelSourceProviderType.AWS_BEDROCK:
        if not llm.bedrock_model or not llm.aws_region:
            return None
        return {
            **_LLM_COMMON_ENV,
            **_LLM_BEDROCK_ENV,
            "SKILL_SCANNER_LLM_MODEL": bedrock_litellm_model_id(llm.bedrock_model),
            "AWS_REGION": llm.aws_region,
        }
    if llm.provider == ModelSourceProviderType.AZURE_OPENAI:
        if not llm.azure_deployment or not llm.azure_endpoint or not llm.azure_api_version:
            return None
        env = {
            **_LLM_COMMON_ENV,
            "SKILL_SCANNER_LLM_MODEL": f"azure/{llm.azure_deployment}",
            "SKILL_SCANNER_LLM_BASE_URL": llm.azure_endpoint,
            "SKILL_SCANNER_LLM_API_VERSION": llm.azure_api_version,
        }
        if llm.azure_api_key is not None:  # key auth; Workload Identity uses DefaultAzureCredential, no key
            key = llm.azure_api_key.get_secret_value()
            env["SKILL_SCANNER_LLM_API_KEY"] = key
            secrets.append(key)
        return env
    return None


def _status(
    *, llm_requested: bool, llm_failed: bool, budget_exceeded: bool, analyzers_failed: bool
) -> tuple[SkillScanResultStatus, SkillScanStatusReason | None]:
    if not llm_requested:
        return SkillScanResultStatus.PARTIAL, SkillScanStatusReason.LLM_UNAVAILABLE
    if llm_failed:
        return SkillScanResultStatus.PARTIAL, SkillScanStatusReason.LLM_FAILED
    if budget_exceeded or analyzers_failed:
        return SkillScanResultStatus.PARTIAL, SkillScanStatusReason.ANALYSIS_INCOMPLETE
    return SkillScanResultStatus.SUCCEEDED, None


def _finding(entry: dict[str, Any]) -> ScanFinding:
    metadata = entry.get("metadata") or {}
    # 2.3.2 reports llm_confidence as a HIGH/MEDIUM/LOW label (llm_response_schema.json), not a 0..1 score, so
    # this is None for its findings today; a numeric value from a future version is still picked up.
    confidence = parse_confidence(metadata.get("llm_confidence"))
    if confidence is None:
        confidence = parse_confidence(metadata.get("meta_confidence"))
    line = coerce_int(entry.get("line_number"))
    return ScanFinding(
        tool=CiscoSkillScannerAdapter.name,
        ruleId=entry.get("rule_id"),
        severity=parse_severity(entry.get("severity"), tool=CiscoSkillScannerAdapter.name),
        toolCategory=entry.get("category"),
        category=map_cisco_category(entry.get("category")),
        title=entry.get("title") or "",
        message=entry.get("description") or "",
        filePath=entry.get("file_path"),
        startLine=line,
        endLine=line,
        snippet=entry.get("snippet"),
        remediation=entry.get("remediation"),
        confidence=confidence,
    )


def _usage(report: dict[str, Any]) -> ScanUsage | None:
    usage = report.get("llm_usage")
    if not isinstance(usage, dict):
        return None
    if usage.get("input_tokens") is None and usage.get("output_tokens") is None:
        return None
    return ScanUsage(
        inputTokens=coerce_int(usage.get("input_tokens")), outputTokens=coerce_int(usage.get("output_tokens"))
    )


def _failed(reason: SkillScanStatusReason, stderr_tail: str) -> ToolOutcome:
    return ToolOutcome(
        status=SkillScanResultStatus.FAILED,
        status_reason=reason,
        findings=[],
        error_message=stderr_tail or None,
    )
