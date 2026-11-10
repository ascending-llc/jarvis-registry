from pathlib import Path

import pytest
from skill_scan_worker import smoke
from skill_scan_worker.adapters.base import ToolOutcome

from registry_pkgs.models.enums import (
    ScanFindingCategory,
    ScanSeverity,
    SkillScanResultStatus,
    SkillScanStatusReason,
)
from registry_pkgs.models.skill_scan_result import ScanFinding


def _finding(**overrides) -> ScanFinding:
    base = {
        "tool": "t",
        "ruleId": "R1",
        "severity": ScanSeverity.CRITICAL,
        "category": ScanFindingCategory.DATA_EXFILTRATION,
        "title": "Exfil",
        "message": "bad",
        "filePath": "SKILL.md",
    }
    base.update(overrides)
    return ScanFinding(**base)


def _outcome(findings: list[ScanFinding], **overrides) -> ToolOutcome:
    base = {
        "status": SkillScanResultStatus.PARTIAL,
        "status_reason": SkillScanStatusReason.LLM_UNAVAILABLE,
        "findings": findings,
    }
    base.update(overrides)
    return ToolOutcome(**base)


def test_passes_on_good_result(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("x")
    assert smoke.check_outcome("t", _outcome([_finding()]), tmp_path) == []


def test_fails_when_no_high_finding(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("x")
    problems = smoke.check_outcome("t", _outcome([_finding(severity=ScanSeverity.LOW)]), tmp_path)
    assert any("no high/critical" in p for p in problems)


def test_fails_on_wrong_status(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("x")
    outcome = _outcome([_finding()], status=SkillScanResultStatus.SUCCEEDED, status_reason=None)
    problems = smoke.check_outcome("t", outcome, tmp_path)
    assert any("status" in p for p in problems)


def test_fails_on_missing_file_path(tmp_path: Path) -> None:
    problems = smoke.check_outcome("t", _outcome([_finding(filePath="does/not/exist.py")]), tmp_path)
    assert any("not under skill dir" in p for p in problems)


@pytest.mark.parametrize("file_path", [None, ""])
def test_fails_when_file_path_absent(tmp_path: Path, file_path: str | None) -> None:
    problems = smoke.check_outcome("t", _outcome([_finding(filePath=file_path)]), tmp_path)
    assert any("has no filePath" in p for p in problems)


def test_fails_on_empty_title(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("x")
    problems = smoke.check_outcome("t", _outcome([_finding(title="")]), tmp_path)
    assert any("empty title" in p for p in problems)


def test_main_resolves_relative_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The scanners run from a per-run directory, so smoke must hand them an absolute path.
    (tmp_path / "skill").mkdir()
    monkeypatch.chdir(tmp_path)
    seen: list[Path] = []

    async def fake_run(skill_dir: Path) -> int:
        seen.append(skill_dir)
        return 0

    monkeypatch.setattr(smoke, "_run", fake_run)
    assert smoke.main(["smoke", "skill"]) == 0
    assert seen == [tmp_path.resolve() / "skill"]


def test_main_rejects_missing_dir() -> None:
    assert smoke.main(["smoke", "/no/such/dir"]) == 2
    assert smoke.main(["smoke"]) == 2
