import subprocess  # nosec B404 — only CompletedProcess/TimeoutExpired; run is faked
from pathlib import Path
from types import SimpleNamespace

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


def test_fails_on_empty_title(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("x")
    problems = smoke.check_outcome("t", _outcome([_finding(title="")]), tmp_path)
    assert any("empty title" in p for p in problems)


def test_main_rejects_missing_dir() -> None:
    assert smoke.main(["/no/such/dir"]) == 2


@pytest.mark.parametrize("file_path", [None, ""])
def test_fails_when_file_path_absent(tmp_path: Path, file_path: str | None) -> None:
    problems = smoke.check_outcome("t", _outcome([_finding(filePath=file_path)]), tmp_path)
    assert any("has no filePath" in p for p in problems)


def test_main_resolves_relative_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "skill").mkdir()
    monkeypatch.chdir(tmp_path)
    seen: list[Path] = []

    async def fake_run(skill_dir: Path) -> int:
        seen.append(skill_dir)
        return 0

    monkeypatch.setattr(smoke, "_run", fake_run)
    assert smoke.main(["skill"]) == 0
    assert seen == [tmp_path.resolve() / "skill"]


def test_main_requires_skill_dir_without_check_versions() -> None:
    with pytest.raises(SystemExit):
        smoke.main([])


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("skill-scanner 2.3.2", "2.3.2"),  # Cisco
        ("SkillSpector v2.13.3", "2.13.3"),  # SkillSpector, leading v stripped
        ("2.3.2", "2.3.2"),  # bare version
        ("skill-scanner 2.3.20", "2.3.20"),  # must stay distinct from 2.3.2
        ("", None),  # empty output
        ("   \n  ", None),  # whitespace only
    ],
)
def test_extract_version(output: str, expected: str | None) -> None:
    assert smoke.extract_version(output) == expected


def _fake_adapter(name: str, tool_version: str, binary: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        tool_version=tool_version,
        build_invocation=lambda *_args: SimpleNamespace(argv=[binary, "scan"]),
    )


def _fake_run(results: dict[str, subprocess.CompletedProcess | Exception], calls: list[list[str]]):
    """A ``subprocess.run`` stand-in: returns or raises the configured result for the binary ``argv[0]``."""

    def run(argv: list[str], **_kwargs) -> subprocess.CompletedProcess:
        calls.append(argv)
        result = results[argv[0]]
        if isinstance(result, Exception):
            raise result
        return result

    return run


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _check(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess | Exception,
    tool_version: str = "2.3.2",
) -> tuple[int, list[list[str]]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(smoke, "ENABLED_ADAPTERS", [_fake_adapter("tool-a", tool_version, "/bin/tool-a")])
    monkeypatch.setattr(smoke.subprocess, "run", _fake_run({"/bin/tool-a": result}, calls))
    return smoke.check_versions(), calls


@pytest.mark.parametrize(
    ("stdout", "tool_version"),
    [
        ("skill-scanner 2.3.2\n", "2.3.2"),  # Cisco
        ("SkillSpector v2.13.3\n", "2.13.3"),  # SkillSpector, leading v
    ],
)
def test_check_versions_passes_on_exact_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdout: str, tool_version: str
) -> None:
    returncode, calls = _check(monkeypatch, _completed(stdout=stdout), tool_version)
    assert returncode == 0
    assert calls == [["/bin/tool-a", "--version"]]  # the adapter's own binary
    assert f"OK   tool-a: {tool_version}" in capsys.readouterr().out


def test_check_versions_reads_stderr_when_stdout_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    returncode, _ = _check(monkeypatch, _completed(stderr="skill-scanner 2.3.2\n"))
    assert returncode == 0


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (_completed(stdout="skill-scanner 2.3.20"), "version '2.3.20' != pinned '2.3.2'"),
        (_completed(stdout=""), "version None != pinned '2.3.2'"),
        (_completed(stdout="skill-scanner 2.3.2", returncode=3), "--version` exited 3"),
        (FileNotFoundError(), "binary not found: /bin/tool-a"),
        (subprocess.TimeoutExpired(cmd="/bin/tool-a", timeout=30), "--version` timed out"),
    ],
    ids=["suffix-mismatch", "no-output", "non-zero-exit", "missing-binary", "timeout"],
)
def test_check_versions_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: subprocess.CompletedProcess | Exception,
    message: str,
) -> None:
    returncode, _ = _check(monkeypatch, result)
    assert returncode == 1
    err = capsys.readouterr().err
    assert "FAIL tool-a:" in err
    assert message in err


def test_check_versions_checks_every_adapter_after_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        smoke,
        "ENABLED_ADAPTERS",
        [_fake_adapter("tool-a", "2.3.2", "/bin/tool-a"), _fake_adapter("tool-b", "2.13.3", "/bin/tool-b")],
    )
    results = {"/bin/tool-a": FileNotFoundError(), "/bin/tool-b": _completed(stdout="SkillSpector v2.13.3")}
    monkeypatch.setattr(smoke.subprocess, "run", _fake_run(results, calls))

    assert smoke.check_versions() == 1
    assert [argv[0] for argv in calls] == ["/bin/tool-a", "/bin/tool-b"]
    captured = capsys.readouterr()
    assert "FAIL tool-a:" in captured.err
    assert "OK   tool-b: 2.13.3" in captured.out


def test_main_check_versions_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "check_versions", lambda: 1)
    assert smoke.main(["--check-versions"]) == 1
