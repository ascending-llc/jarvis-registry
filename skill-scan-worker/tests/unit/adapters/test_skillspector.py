from pathlib import Path

import pytest
from beanie import PydanticObjectId
from pydantic import SecretStr
from skill_scan_worker.adapters.skillspector import SkillSpectorAdapter
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import ProcessResult

from registry_pkgs.models.enums import (
    ModelSourceProviderType,
    ScanSeverity,
    SkillScanResultStatus,
    SkillScanStatusReason,
)
from registry_pkgs.models.skill_scan_result import ScanLLMInfo

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "skillspector"
_ADAPTER = SkillSpectorAdapter()


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


def _bedrock_llm(model: str, *, base_model_id: str = "anthropic.claude-v1") -> ResolvedLLM:
    return ResolvedLLM(
        info=_llm_info(ModelSourceProviderType.AWS_BEDROCK),
        provider=ModelSourceProviderType.AWS_BEDROCK,
        base_model_id=base_model_id,
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
    assert outcome.tool_metadata["riskScore"] == 92


def test_clean_nollm() -> None:
    outcome = _ADAPTER.parse(_process("clean-nollm.json"), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_UNAVAILABLE
    assert outcome.findings == []


def test_malicious_llm_succeeds_with_usage() -> None:
    outcome = _ADAPTER.parse(_process("malicious-llm.json"), llm_requested=True)
    assert outcome.status == SkillScanResultStatus.SUCCEEDED
    assert outcome.usage is not None
    assert outcome.usage.inputTokens == 2100 + 1540
    assert outcome.usage.outputTokens == 180 + 95


def test_llm_failed() -> None:
    outcome = _ADAPTER.parse(_process("llm-failed.json"), llm_requested=True)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_FAILED


def test_duplicate_issues_collapse() -> None:
    outcome = _ADAPTER.parse(_process("malicious-nollm.json"), llm_requested=False)
    keys = [(f.ruleId, f.filePath, f.startLine) for f in outcome.findings]
    assert len(keys) == len(set(keys))
    assert ("SC1", "scripts/collect.py", 14) in keys  # the two SC1 rows became one


def test_non_json_is_parse_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.out"
    bad.write_text("<<<")
    process = ProcessResult(
        exit_code=1, timed_out=False, output_file=bad, output_too_large=False, stderr_tail="", duration_ms=1
    )
    outcome = _ADAPTER.parse(process, llm_requested=False)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.PARSE_ERROR


def test_exit_2_is_tool_error() -> None:
    outcome = _ADAPTER.parse(_process("malicious-nollm.json", exit_code=2), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.TOOL_ERROR


def test_exit_1_with_valid_json_is_not_a_failure() -> None:
    outcome = _ADAPTER.parse(_process("malicious-nollm.json", exit_code=1), llm_requested=False)
    assert outcome.status != SkillScanResultStatus.FAILED


def test_malformed_usage_does_not_fail_parse(tmp_path: Path) -> None:
    # A bad token value must contribute 0, not turn the whole scan into a parse error.
    report = tmp_path / "r.json"
    report.write_text(
        '{"metadata": {"llm_available": true, "inference_usage": '
        '[{"input_tokens": "oops", "output_tokens": null}, {"input_tokens": 50, "output_tokens": 7}]},'
        '"analysis_completeness": {"is_complete": true}, "issues": []}'
    )
    process = ProcessResult(
        exit_code=0, timed_out=False, output_file=report, output_too_large=False, stderr_tail="", duration_ms=1
    )
    outcome = _ADAPTER.parse(process, llm_requested=True)
    assert outcome.status == SkillScanResultStatus.SUCCEEDED
    assert outcome.usage is not None
    assert outcome.usage.inputTokens == 50
    assert outcome.usage.outputTokens == 7


# ---- build_invocation ----


def test_invocation_none_llm_passes_no_llm() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), None)
    assert "--no-llm" in inv.argv
    assert inv.llm_used is False
    assert "SKILLSPECTOR_PROVIDER" not in inv.env
    assert inv.env["SKILLSPECTOR_MAX_LLM_CONCURRENCY"] == "2"
    assert inv.env["SKILLSPECTOR_MAX_WORKFLOW_SECONDS"] == "540"


def test_invocation_bedrock_plain_id() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _bedrock_llm("amazon.nova-lite-v1:0"))
    assert "--no-llm" not in inv.argv
    assert inv.llm_used is True
    assert inv.env["SKILLSPECTOR_PROVIDER"] == "bedrock"
    assert inv.env["SKILLSPECTOR_MODEL"] == "amazon.nova-lite-v1:0"
    assert inv.env["AWS_REGION"] == "us-east-1"


def test_invocation_bedrock_anthropic_arn_is_usable() -> None:
    arn = "arn:aws:bedrock:us-east-1:1:application-inference-profile/p"
    inv = _ADAPTER.build_invocation(
        Path("/skill"), Path("/out.json"), _bedrock_llm(arn, base_model_id="anthropic.claude-sonnet")
    )
    assert "--no-llm" not in inv.argv
    assert inv.env["SKILLSPECTOR_PROVIDER"] == "bedrock"


def test_invocation_bedrock_non_anthropic_arn_falls_back_to_no_llm() -> None:
    arn = "arn:aws:bedrock:us-east-1:1:application-inference-profile/p"
    inv = _ADAPTER.build_invocation(
        Path("/skill"), Path("/out.json"), _bedrock_llm(arn, base_model_id="amazon.nova-lite-v1:0")
    )
    assert "--no-llm" in inv.argv
    assert inv.llm_used is False
    assert "SKILLSPECTOR_PROVIDER" not in inv.env


def test_invocation_azure_with_key() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _azure_llm(key="secret-key"))
    assert "--no-llm" not in inv.argv
    assert inv.env["SKILLSPECTOR_PROVIDER"] == "azure_openai"
    assert inv.env["AZURE_OPENAI_API_KEY"] == "secret-key"
    assert inv.env["AZURE_OPENAI_DEPLOYMENT"] == "chat"
    assert inv.env["SKILLSPECTOR_MODEL"] == "gpt-4o"
    assert "secret-key" in inv.secrets
    assert "secret-key" not in " ".join(inv.argv)


def test_invocation_azure_workload_identity_falls_back_to_no_llm() -> None:
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _azure_llm(key=None))
    assert "--no-llm" in inv.argv
    assert inv.llm_used is False
    assert "SKILLSPECTOR_PROVIDER" not in inv.env


def test_invocation_never_passes_forbidden_flags() -> None:
    for llm in (None, _bedrock_llm("amazon.nova-lite-v1:0"), _azure_llm(key="k")):
        inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), llm)
        joined = " ".join(inv.argv)
        for flag in ("--transitive", "--use-shipped-baseline", "--exclude", "--fail-on"):
            assert flag not in joined


def test_static_fingerprint_differs_from_cisco() -> None:
    from skill_scan_worker.adapters.cisco import CiscoSkillScannerAdapter

    assert _ADAPTER.static_fingerprint() != CiscoSkillScannerAdapter().static_fingerprint()
    assert _ADAPTER.static_fingerprint() == _ADAPTER.static_fingerprint()


@pytest.mark.parametrize("fixture", ["malicious-nollm.json", "clean-nollm.json", "malicious-llm.json"])
def test_fixtures_are_parseable(fixture: str) -> None:
    outcome = _ADAPTER.parse(_process(fixture), llm_requested=True)
    assert outcome.status != SkillScanResultStatus.FAILED
