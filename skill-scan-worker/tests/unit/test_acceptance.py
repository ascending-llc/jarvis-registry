import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from skill_scan_worker.adapters.base import ToolOutcome

from registry_pkgs.models.enums import ScanFindingCategory, ScanSeverity, SkillScanResultStatus, SkillScanStatusReason
from registry_pkgs.models.skill_scan_result import ScanFinding


@pytest.mark.parametrize(
    ("status", "reason", "verdict", "exit_code"),
    [
        (SkillScanResultStatus.FAILED, SkillScanStatusReason.TOOL_ERROR, "FAIL", 1),
        (SkillScanResultStatus.FAILED, SkillScanStatusReason.PARSE_ERROR, "FAIL", 1),
        (SkillScanResultStatus.PARTIAL, SkillScanStatusReason.LLM_FAILED, "BLOCKED", 0),
        (SkillScanResultStatus.PARTIAL, SkillScanStatusReason.ANALYSIS_INCOMPLETE, "BLOCKED", 0),
    ],
)
async def test_llm_tool_failure_fails_acceptance_even_when_invalid_model_check_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: SkillScanResultStatus,
    reason: SkillScanStatusReason,
    verdict: str,
    exit_code: int,
) -> None:
    path = Path(__file__).resolve().parents[2] / "scripts" / "acceptance.py"
    spec = importlib.util.spec_from_file_location("acceptance", path)
    assert spec is not None and spec.loader is not None
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    (tmp_path / "SKILL.md").write_text("fixture")
    finding = ScanFinding(
        tool="test",
        severity=ScanSeverity.HIGH,
        category=ScanFindingCategory.OTHER,
        title="Risk",
        message="Risk",
        filePath="SKILL.md",
    )
    no_llm = {"status": SkillScanResultStatus.PARTIAL, "status_reason": SkillScanStatusReason.LLM_UNAVAILABLE}
    scan = AsyncMock(
        side_effect=[
            ToolOutcome(**no_llm, findings=[finding]),
            ToolOutcome(**no_llm, findings=[]),
            ToolOutcome(status=status, status_reason=reason, findings=[]),
            ToolOutcome(
                status=SkillScanResultStatus.FAILED, status_reason=SkillScanStatusReason.TOOL_ERROR, findings=[]
            ),
        ]
    )
    monkeypatch.setattr(acceptance, "ENABLED_ADAPTERS", [SimpleNamespace(name="test")])
    monkeypatch.setattr(acceptance, "MALICIOUS", tmp_path)
    monkeypatch.setattr(acceptance, "MODEL", "valid-model")
    monkeypatch.setattr(acceptance, "_llm", lambda model: model)
    monkeypatch.setattr(acceptance, "_scan", scan)

    assert await acceptance._run() == exit_code
    assert scan.await_count == 4
    assert [(scenario, result) for scenario, result, _ in acceptance._results] == [
        ("malicious/no-llm", "PASS"),
        ("clean/no-llm", "PASS"),
        ("malicious/llm", verdict),
        ("invalid-model/llm", "PASS"),
    ]
