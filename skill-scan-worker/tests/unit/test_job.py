"""run_adapter normalization/redaction/truncation tests, driven by a fake adapter over a real tiny subprocess."""

import sys
from pathlib import Path

from beanie import PydanticObjectId
from skill_scan_worker.adapters.base import ToolOutcome
from skill_scan_worker.job import _normalize_findings, _redact, run_adapter
from skill_scan_worker.process import Invocation

from registry_pkgs.models.enums import ScanFindingCategory, ScanSeverity, SkillScanResultStatus, SkillScanStatusReason
from registry_pkgs.models.skill_scan_result import ScanFinding

_DIGEST = "sha256:deadbeef"


class _FakeAdapter:
    name = "fake-scanner"
    tool_version = "1.0.0"
    llm_input_unbounded = True

    def __init__(self, outcome: ToolOutcome, *, secrets: tuple[str, ...] = ()) -> None:
        self._outcome = outcome
        self._secrets = secrets

    def static_fingerprint(self) -> str:
        return "sha256:static"

    def build_invocation(self, skill_dir: Path, output_file: Path, llm) -> Invocation:
        # A trivial, fast process; parse ignores its output.
        return Invocation(argv=[sys.executable, "-c", "pass"], output_file=output_file, secrets=self._secrets)

    def parse(self, process, *, llm_requested: bool) -> ToolOutcome:
        return self._outcome


def _finding(severity: ScanSeverity, file_path: str | None = None) -> ScanFinding:
    return ScanFinding(
        tool="fake-scanner",
        severity=severity,
        category=ScanFindingCategory.OTHER,
        title="t",
        message="m",
        filePath=file_path,
    )


async def _run(adapter, tmp_path: Path, *, size_skipped: bool = False):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    return await run_adapter(
        adapter,
        skill_dir,
        tmp_path,
        None,
        size_skipped,
        "sha256:fp",
        None,
        skill_id=PydanticObjectId(),
        skill_version=1,
        content_digest=_DIGEST,
    )


async def test_findings_truncated_to_most_severe_200(tmp_path: Path) -> None:
    findings = [_finding(ScanSeverity.LOW) for _ in range(249)] + [_finding(ScanSeverity.CRITICAL)]
    adapter = _FakeAdapter(ToolOutcome(SkillScanResultStatus.SUCCEEDED, None, findings))
    result = await _run(adapter, tmp_path)
    assert len(result.findings) == 200
    assert result.findingsTruncated is True
    # The single critical finding survives the severity-descending sort.
    assert any(ScanSeverity(f.severity) == ScanSeverity.CRITICAL for f in result.findings)


async def test_api_key_redacted_in_error_message(tmp_path: Path) -> None:
    outcome = ToolOutcome(
        SkillScanResultStatus.FAILED, SkillScanStatusReason.TOOL_ERROR, [], error_message="boom key=SECRET123 end"
    )
    adapter = _FakeAdapter(outcome, secrets=("SECRET123",))
    result = await _run(adapter, tmp_path)
    assert "SECRET123" not in (result.errorMessage or "")
    assert "***" in (result.errorMessage or "")


async def test_size_skipped_forces_partial(tmp_path: Path) -> None:
    adapter = _FakeAdapter(ToolOutcome(SkillScanResultStatus.SUCCEEDED, None, []))
    result = await _run(adapter, tmp_path, size_skipped=True)
    assert result.status == SkillScanResultStatus.PARTIAL
    assert result.statusReason == SkillScanStatusReason.LLM_SKIPPED_SIZE_LIMIT


async def test_absolute_finding_path_made_relative(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    findings = [_finding(ScanSeverity.HIGH, file_path=str(skill_dir / "SKILL.md"))]
    normalized = _normalize_findings(findings, skill_dir)
    assert normalized[0].filePath == "SKILL.md"


def test_redact_handles_none_and_multiple_secrets() -> None:
    assert _redact(None, ("x",)) is None
    assert _redact("a b c", ("a", "c")) == "*** b ***"
