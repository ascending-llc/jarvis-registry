import json
from pathlib import Path

import pytest
from beanie import PydanticObjectId
from pydantic import SecretStr
from skill_scan_worker.adapters.base import ToolOutcome
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


def _process(fixture: str, *, exit_code: int | None = 0, stderr: str = "") -> ProcessResult:
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


# Exit codes the recorder observed for each fixture (v2.13.3 exits 1 when the risk score is above 50).
_RECORDED_EXIT = {"malicious-nollm.json": 1, "clean-nollm.json": 0, "malicious-llm.json": 1, "llm-failed.json": 2}


def _recorded(fixture: str) -> ProcessResult:
    return _process(fixture, exit_code=_RECORDED_EXIT[fixture])


def _parse_modified(tmp_path: Path, fixture: str, **metadata: object) -> ToolOutcome:
    """Parse a recorded report with some ``metadata`` fields overridden (a real report shape, other outcome)."""
    report = json.loads((_FIXTURES / fixture).read_text())
    report["metadata"].update(metadata)
    path = tmp_path / fixture
    path.write_text(json.dumps(report))
    process = ProcessResult(
        exit_code=_RECORDED_EXIT[fixture],
        timed_out=False,
        output_file=path,
        output_too_large=False,
        stderr_tail="",
        duration_ms=1,
    )
    return _ADAPTER.parse(process, llm_requested=True)


def test_malicious_nollm() -> None:
    outcome = _ADAPTER.parse(_recorded("malicious-nollm.json"), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_UNAVAILABLE
    assert any(f.severity in (ScanSeverity.HIGH, ScanSeverity.CRITICAL) for f in outcome.findings)
    assert outcome.tool_metadata["riskScore"] == 91


def test_clean_nollm() -> None:
    outcome = _ADAPTER.parse(_recorded("clean-nollm.json"), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_UNAVAILABLE
    assert outcome.findings == []


def test_malicious_llm_succeeds_with_usage() -> None:
    outcome = _ADAPTER.parse(_recorded("malicious-llm.json"), llm_requested=True)
    assert outcome.status == SkillScanResultStatus.SUCCEEDED
    assert outcome.status_reason is None
    assert outcome.usage is not None
    assert outcome.usage.inputTokens == 16708
    assert outcome.usage.outputTokens == 3455


def test_bedrock_llm_available_false_is_ignored_when_every_call_succeeded() -> None:
    # v2.13.3 reports llm_available=false for every Bedrock scan (its report-time check takes the API-key path,
    # which SigV4 Bedrock never satisfies). The recording shows it next to 5 of 5 successful calls.
    metadata = json.loads((_FIXTURES / "malicious-llm.json").read_text())["metadata"]
    assert metadata["llm_available"] is False
    assert metadata["llm_calls_attempted"] == metadata["llm_calls_succeeded"] == 5
    assert "No LLM API key configured" in metadata["llm_error"]
    assert _ADAPTER.parse(_recorded("malicious-llm.json"), llm_requested=True).status == SkillScanResultStatus.SUCCEEDED


@pytest.mark.parametrize(
    "metadata",
    [
        {"llm_calls_succeeded": 4, "llm_degraded": True},  # one batch failed (e.g. a content-filtered reply)
        {"llm_calls_succeeded": 4},  # a failed call even without the degraded flag
        {"llm_calls_attempted": 0, "llm_calls_succeeded": 0},  # the LLM never ran
    ],
)
def test_lost_llm_calls_are_llm_failed(tmp_path: Path, metadata: dict) -> None:
    outcome = _parse_modified(tmp_path, "malicious-llm.json", **metadata)
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_FAILED
    assert outcome.findings  # the static findings are kept


def test_without_call_counters_llm_available_false_is_llm_failed(tmp_path: Path) -> None:
    outcome = _parse_modified(tmp_path, "malicious-llm.json", llm_calls_attempted=None, llm_calls_succeeded=None)
    assert outcome.status_reason == SkillScanStatusReason.LLM_FAILED


def test_every_llm_call_failed_is_tool_error() -> None:
    # Recorded with an invalid model id: v2.13.3 marks the LLM ledger exceptions fatal, sets
    # execution_successful=false and exits 2 after writing the report, so it is a tool error, not llm_failed.
    report = json.loads((_FIXTURES / "llm-failed.json").read_text())
    assert report["execution_successful"] is False
    assert report["metadata"]["llm_calls_succeeded"] == 0
    outcome = _ADAPTER.parse(_recorded("llm-failed.json"), llm_requested=True)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.TOOL_ERROR


def test_duplicate_issues_collapse() -> None:
    report = json.loads((_FIXTURES / "malicious-nollm.json").read_text())
    raw_keys = [(i["id"], i["location"]["file"], i["location"]["start_line"]) for i in report["issues"]]
    assert raw_keys.count(("E1", "scripts/collect.py", 13)) == 2  # the recording repeats E1 on one line
    outcome = _ADAPTER.parse(_recorded("malicious-nollm.json"), llm_requested=False)
    keys = [(f.ruleId, f.filePath, f.startLine) for f in outcome.findings]
    assert len(keys) == len(set(keys))
    assert ("E1", "scripts/collect.py", 13) in keys


def test_non_json_is_parse_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.out"
    bad.write_text("<<<")
    process = ProcessResult(
        exit_code=1, timed_out=False, output_file=bad, output_too_large=False, stderr_tail="", duration_ms=1
    )
    outcome = _ADAPTER.parse(process, llm_requested=False)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.PARSE_ERROR


def test_forced_error_exit_is_tool_error_with_stderr() -> None:
    # Recorded: scanning a missing directory exits 2 and writes no report.
    stderr = (_FIXTURES / "forced-error.stderr.txt").read_text()
    assert stderr.startswith("Error: Cannot determine input type for:")
    outcome = _ADAPTER.parse(_process("forced-error.json", exit_code=2, stderr=stderr), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.TOOL_ERROR
    assert outcome.error_message == stderr


@pytest.mark.parametrize("exit_code", [2, -9, 137, None])
def test_exit_outside_0_and_1_is_tool_error(exit_code: int | None) -> None:
    # The report file is complete here; a signal kill after writing it still is not a successful scan.
    outcome = _ADAPTER.parse(_process("malicious-nollm.json", exit_code=exit_code), llm_requested=False)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.TOOL_ERROR


def test_exit_1_with_valid_json_is_not_a_failure() -> None:
    outcome = _ADAPTER.parse(_process("malicious-nollm.json", exit_code=1), llm_requested=False)
    assert outcome.status != SkillScanResultStatus.FAILED


def _parse_inline(tmp_path: Path, report_json: dict) -> ToolOutcome:
    report = tmp_path / "r.json"
    report.write_text(json.dumps(report_json))
    process = ProcessResult(
        exit_code=0, timed_out=False, output_file=report, output_too_large=False, stderr_tail="", duration_ms=1
    )
    return _ADAPTER.parse(process, llm_requested=True)


@pytest.mark.parametrize("report", [{}, {"issues": None}, {"issues": {}}])
def test_report_without_issues_array_is_parse_error(tmp_path: Path, report: dict) -> None:
    report = {"metadata": {"llm_available": True}, "analysis_completeness": {"is_complete": True}} | report
    outcome = _parse_inline(tmp_path, report)
    assert outcome.status == SkillScanResultStatus.FAILED
    assert outcome.status_reason == SkillScanStatusReason.PARSE_ERROR


def test_malformed_usage_does_not_fail_parse(tmp_path: Path) -> None:
    # A bad token value must contribute 0, not turn the whole scan into a parse error.
    outcome = _parse_inline(
        tmp_path,
        {
            "metadata": {
                "llm_available": True,
                "inference_usage": [
                    {"prompt_tokens": "oops", "completion_tokens": 3},
                    {"prompt_tokens": 50, "completion_tokens": 7},
                ],
            },
            "analysis_completeness": {"is_complete": True},
            "issues": [],
        },
    )
    assert outcome.status == SkillScanResultStatus.SUCCEEDED
    assert outcome.usage is not None
    assert outcome.usage.inputTokens == 50
    assert outcome.usage.outputTokens == 10


@pytest.mark.parametrize(
    "inference_usage",
    [
        [],  # SkillSpector's "provider supplied no counters", not zero cost
        [{"node": "meta_analyzer", "usage_source": "provider_response"}],
        [{"input_tokens": 50, "output_tokens": 7}],  # not v2.13.3's counter names
    ],
)
def test_usage_is_none_without_counters(tmp_path: Path, inference_usage: list) -> None:
    outcome = _parse_inline(
        tmp_path,
        {
            "metadata": {"llm_available": True, "inference_usage": inference_usage},
            "analysis_completeness": {"is_complete": True},
            "issues": [],
        },
    )
    assert outcome.usage is None


def test_llm_degraded_is_llm_failed(tmp_path: Path) -> None:
    # Failed semantic batches leave llm_available true and the analysis complete; only llm_degraded says so.
    outcome = _parse_inline(
        tmp_path,
        {
            "metadata": {"llm_available": True, "llm_degraded": True, "inference_usage": []},
            "analysis_completeness": {"is_complete": True},
            "issues": [],
        },
    )
    assert outcome.status == SkillScanResultStatus.PARTIAL
    assert outcome.status_reason == SkillScanStatusReason.LLM_FAILED


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


@pytest.mark.parametrize(
    ("model", "base_model_id"),
    [
        ("amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"),
        ("arn:aws:bedrock:us-east-1:1:application-inference-profile/p", "anthropic.claude-sonnet-5-5"),
    ],
)
def test_invocation_bedrock_uses_json_schema_output(model: str, base_model_id: str) -> None:
    # The default forced tool call is rejected by Claude Sonnet 5.5, failing every LLM batch.
    inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), _bedrock_llm(model, base_model_id=base_model_id))
    assert inv.env["SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD"] == "json_schema"


@pytest.mark.parametrize("key", ["k", None])
def test_invocation_without_bedrock_llm_keeps_default_output_method(key: str | None) -> None:
    for llm in (None, _azure_llm(key=key)):
        inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), llm)
        assert "SKILLSPECTOR_STRUCTURED_OUTPUT_METHOD" not in inv.env


def test_invocation_never_sets_temperature() -> None:
    # v2.13.3 sends no temperature unless asked; newer models reject any value.
    for llm in (None, _bedrock_llm("amazon.nova-lite-v1:0"), _azure_llm(key="k")):
        inv = _ADAPTER.build_invocation(Path("/skill"), Path("/out.json"), llm)
        assert "SKILLSPECTOR_TEMPERATURE" not in inv.env


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
