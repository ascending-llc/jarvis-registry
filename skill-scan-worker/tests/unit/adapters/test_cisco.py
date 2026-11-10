import json
from pathlib import Path

import pytest
from beanie import PydanticObjectId
from pydantic import SecretStr
from skill_scan_worker.adapters.cisco import CiscoSkillScannerAdapter
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import ProcessResult

from registry_pkgs.models.enums import (
    ModelSourceProviderType,
    ScanSeverity,
    SkillScanResultStatus,
    SkillScanStatusReason,
)
from registry_pkgs.models.skill_scan_result import ScanLLMInfo

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "cisco"
_ADAPTER = CiscoSkillScannerAdapter()


def _process(fixture: str, *, exit_code: int = 0, stderr: str = "") -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        timed_out=False,
        output_file=_FIXTURES / fixture,
        output_too_large=False,
        stderr_tail=stderr,
        duration_ms=1,
    )


def _llm_info(provider: ModelSourceProviderType) -> ScanLLMInfo:
    return ScanLLMInfo(modelSourceId=PydanticObjectId(), providerType=provider, modelId="m")


def _bedrock_llm(model: str) -> ResolvedLLM:
    return ResolvedLLM(
        info=_llm_info(ModelSourceProviderType.AWS_BEDROCK),
        provider=ModelSourceProviderType.AWS_BEDROCK,
        base_model_id="anthropic.claude-v1",
        aws_region="us-east-1",
        bedrock_model=model,
    )


def _azure_llm(*, key: str | None) -> ResolvedLLM:
    return ResolvedLLM(
        info=_llm_info(ModelSourceProviderType.AZURE_OPENAI),
        provider=ModelSourceProviderType.AZURE_OPENAI,
        base_model_id="gpt-4o",
        azure_endpoint="https://example.openai.azure.com",
        azure_deployment="chat",
        azure_api_version="2024-10-21",
        azure_api_key=SecretStr(key) if key is not None else None,
    )


# ---- parse ----


def test_malicious_nollm() -> None:
    outcome = _ADAPTER.parse(_process("malicious-nollm.json"), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_UNAVAILABLE
    assert any(f.severity in (ScanSeverity.HIGH, ScanSeverity.CRITICAL) for f in outcome.findings)
    assert outcome.tool_metadata["isSafe"] is False


def test_clean_nollm() -> None:
    outcome = _ADAPTER.parse(_process("clean-nollm.json"), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_UNAVAILABLE
    assert not any(f.severity in (ScanSeverity.HIGH, ScanSeverity.CRITICAL) for f in outcome.findings)


def test_malicious_llm_succeeds_with_usage() -> None:
    outcome = _ADAPTER.parse(_process("malicious-llm.json"), llm_requested=True)
    assert outcome.status == SkillScanResultStatus.SUCCEEDED
    assert outcome.status_reason is None
    assert outcome.usage is not None
    assert outcome.usage.inputTokens == 4120
    assert outcome.usage.outputTokens == 388
    assert any(f.confidence == 0.95 for f in outcome.findings)


def test_llm_failed() -> None:
    outcome = _ADAPTER.parse(_process("llm-failed.json"), llm_requested=True)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_FAILED


def test_status_signal_rules_are_dropped() -> None:
    outcome = _ADAPTER.parse(_process("llm-failed.json"), llm_requested=True)
    rule_ids = {f.ruleId for f in outcome.findings}
    assert "LLM_ANALYSIS_FAILED" not in rule_ids
    assert "LLM_CONTEXT_BUDGET_EXCEEDED" not in rule_ids
    assert "BEH_EXFIL_001" in rule_ids  # a real finding survives


def test_non_json_is_parse_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.out"
    bad.write_text("not json")
    process = ProcessResult(
        exit_code=0, timed_out=False, output_file=bad, output_too_large=False, stderr_tail="boom", duration_ms=1
    )
    outcome = _ADAPTER.parse(process, llm_requested=False)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.PARSE_ERROR
    assert outcome.error_message == "boom"


def test_malformed_line_and_usage_do_not_fail_parse(tmp_path: Path) -> None:
    # A bad line_number / token value degrades to None, not a whole-scan parse error.
    report = tmp_path / "r.json"
    report.write_text(
        '{"llm_usage": {"input_tokens": "oops", "output_tokens": 9},'
        '"findings": [{"rule_id": "X", "severity": "high", "category": "data_exfiltration",'
        '"title": "t", "description": "d", "file_path": "SKILL.md", "line_number": "12-14"}]}'
    )
    process = ProcessResult(
        exit_code=0, timed_out=False, output_file=report, output_too_large=False, stderr_tail="", duration_ms=1
    )
    outcome = _ADAPTER.parse(process, llm_requested=True)
    assert outcome.status != SkillScanResultStatus.FAILED
    assert outcome.findings[0].startLine is None
    assert outcome.usage is not None
    assert outcome.usage.inputTokens is None
    assert outcome.usage.outputTokens == 9


@pytest.mark.parametrize("report", [{}, {"findings": None}, {"findings": {}}, {"error": "boom"}])
def test_report_without_findings_array_is_parse_error(tmp_path: Path, report: dict) -> None:
    out = tmp_path / "r.json"
    out.write_text(json.dumps(report))
    process = ProcessResult(
        exit_code=0, timed_out=False, output_file=out, output_too_large=False, stderr_tail="", duration_ms=1
    )
    outcome = _ADAPTER.parse(process, llm_requested=True)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.PARSE_ERROR


def test_exit_1_is_tool_error() -> None:
    outcome = _ADAPTER.parse(_process("malicious-nollm.json", exit_code=1, stderr="crash"), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.TOOL_ERROR


# ---- build_invocation ----


def test_invocation_none_llm() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), None)
    assert "--use-llm" not in inv.argv
    assert inv.llm_used is False
    assert inv.env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert inv.argv[0].endswith("/cisco/bin/skill-scanner")
    assert inv.argv[1:3] == ["scan", "/skill"]
    assert "--use-behavioral" in inv.argv and "--use-osv" in inv.argv


def test_invocation_bedrock_plain_id() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _bedrock_llm("amazon.nova-lite-v1:0"))
    assert "--use-llm" in inv.argv
    assert inv.llm_used is True
    assert inv.env["SKILL_SCANNER_LLM_MODEL"] == "bedrock/amazon.nova-lite-v1:0"
    assert inv.env["AWS_REGION"] == "us-east-1"


def test_invocation_bedrock_aip_arn_uses_converse() -> None:
    arn = "arn:aws:bedrock:us-east-1:1:application-inference-profile/p"
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _bedrock_llm(arn))
    assert inv.env["SKILL_SCANNER_LLM_MODEL"] == f"bedrock/converse/{arn}"


def test_invocation_azure_with_key() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _azure_llm(key="secret-key"))
    assert "--use-llm" in inv.argv
    assert inv.env["SKILL_SCANNER_LLM_MODEL"] == "azure/chat"
    assert inv.env["SKILL_SCANNER_LLM_BASE_URL"] == "https://example.openai.azure.com"
    assert inv.env["SKILL_SCANNER_LLM_API_VERSION"] == "2024-10-21"
    assert inv.env["SKILL_SCANNER_LLM_API_KEY"] == "secret-key"
    assert "secret-key" in inv.secrets
    assert "secret-key" not in " ".join(inv.argv)


def test_invocation_azure_workload_identity_has_no_key() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _azure_llm(key=None))
    assert "--use-llm" in inv.argv
    assert "SKILL_SCANNER_LLM_API_KEY" not in inv.env
    assert inv.secrets == ()


def test_invocation_never_passes_forbidden_flags() -> None:
    for llm in (None, _bedrock_llm("amazon.nova-lite-v1:0"), _azure_llm(key="k")):
        inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), llm)
        joined = " ".join(inv.argv)
        for flag in ("--use-virustotal", "--use-aidefense", "--fail-on", "--enable-meta", "--transitive"):
            assert flag not in joined


def test_static_fingerprint_is_stable() -> None:
    assert _ADAPTER.static_fingerprint() == _ADAPTER.static_fingerprint()


def test_static_fingerprint_changes_with_config() -> None:
    from skill_scan_worker.adapters.base import static_fingerprint_for

    base = static_fingerprint_for("cisco-skill-scanner", "2.3.2", {"policy": "balanced"})
    changed = static_fingerprint_for("cisco-skill-scanner", "2.3.2", {"policy": "strict"})
    assert base != changed


@pytest.mark.parametrize("fixture", ["malicious-nollm.json", "clean-nollm.json", "malicious-llm.json"])
def test_fixtures_are_parseable(fixture: str) -> None:
    # Guards the committed fixtures against accidental corruption.
    outcome = _ADAPTER.parse(_process(fixture), llm_requested=True)
    assert outcome.status != SkillScanResultStatus.FAILED
