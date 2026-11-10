"""In-image smoke check: run every enabled adapter's real tool against a skill dir, with no LLM, and
assert each returns a usable outcome.

A version bump that breaks a tool's report format must fail here rather than silently return zero
findings. Runs offline (no MongoDB): it validates each adapter's ``ToolOutcome`` directly via
``build_invocation`` -> ``run_tool_process`` -> ``parse``, bypassing ``run_adapter``, which would
construct a Beanie ``SkillScanResult`` and need an initialized database. Run inside the built image
(pytest is not installed there); CI runs it on every image build:

    python -m skill_scan_worker.smoke <skill_dir>
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from registry_pkgs.models.enums import ScanSeverity, SkillScanResultStatus, SkillScanStatusReason
from skill_scan_worker.adapters import ENABLED_ADAPTERS
from skill_scan_worker.adapters.base import ToolOutcome
from skill_scan_worker.config import settings
from skill_scan_worker.process import run_tool_process

_HIGH_SEVERITIES = frozenset({ScanSeverity.HIGH, ScanSeverity.CRITICAL})


def check_outcome(tool: str, outcome: ToolOutcome, skill_dir: Path) -> list[str]:
    """Return the reasons ``outcome`` fails the smoke contract; empty means it passes."""
    problems: list[str] = []
    if outcome.status != SkillScanResultStatus.PARTIAL:
        problems.append(f"status {outcome.status} != partial")
    if outcome.status_reason != SkillScanStatusReason.LLM_UNAVAILABLE:
        problems.append(f"status_reason {outcome.status_reason} != llm_unavailable")
    if not any(f.severity in _HIGH_SEVERITIES for f in outcome.findings):
        problems.append("no high/critical finding")
    valid_severities = set(ScanSeverity)
    for finding in outcome.findings:
        if not finding.title:
            problems.append(f"finding {finding.ruleId} has empty title")
        if finding.severity not in valid_severities:
            problems.append(f"finding {finding.ruleId} has invalid severity {finding.severity}")
        if not finding.filePath:
            problems.append(f"finding {finding.ruleId} has no filePath")
        elif not (skill_dir / finding.filePath).exists():
            problems.append(f"finding {finding.ruleId} filePath {finding.filePath} not under skill dir")
    return problems


async def _scan(adapter, skill_dir: Path) -> ToolOutcome:
    run_dir = Path(tempfile.mkdtemp(dir=settings.work_dir))
    output_file = run_dir / f"{adapter.name}.out"
    invocation = adapter.build_invocation(skill_dir, output_file, None)
    process = await run_tool_process(
        invocation, cwd=run_dir, timeout=settings.tool_timeout_seconds, max_output_bytes=settings.max_output_bytes
    )
    return adapter.parse(process, llm_requested=invocation.llm_used)


async def _run(skill_dir: Path) -> int:
    failed = False
    for adapter in ENABLED_ADAPTERS:
        outcome = await _scan(adapter, skill_dir)
        problems = check_outcome(adapter.name, outcome, skill_dir)
        if problems:
            failed = True
            print(f"FAIL {adapter.name}: {'; '.join(problems)}", file=sys.stderr)
            if outcome.error_message:
                print(f"  error_message: {outcome.error_message}", file=sys.stderr)
        else:
            print(f"OK   {adapter.name}: {len(outcome.findings)} finding(s)")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m skill_scan_worker.smoke <skill_dir>", file=sys.stderr)
        return 2
    skill_dir = Path(argv[1])
    if not skill_dir.is_dir():
        print(f"not a directory: {skill_dir}", file=sys.stderr)
        return 2
    return asyncio.run(_run(skill_dir))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
