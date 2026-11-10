"""In-image smoke check for the built scanner image. Two modes, both offline (no MongoDB):

``python -m skill_scan_worker.smoke <skill_dir>``
    Run every enabled adapter's real tool against a skill dir with no LLM and assert each returns a
    usable outcome. A version bump that breaks a tool's report format fails here rather than silently
    returning zero findings at runtime. Validates each adapter's ``ToolOutcome`` directly via
    ``build_invocation`` -> ``run_tool_process`` -> ``parse``, bypassing ``run_adapter`` (which would
    construct a Beanie ``SkillScanResult`` and need an initialized database).

``python -m skill_scan_worker.smoke --check-versions``
    Build-time guard: for each enabled adapter, run its tool binary's ``--version`` and require the
    output's last whitespace token (minus a leading ``v``) to equal the adapter's ``tool_version``. The
    adapter's ``tool_version`` is the single source of the pinned version; the Dockerfile has none. The
    binary comes from ``build_invocation(...).argv[0]`` with ``llm=None``, so the path is the adapter's.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess  # nosec B404 — only used to run the pinned scanner binaries' --version
import sys
import tempfile
from pathlib import Path

from registry_pkgs.models.enums import ScanSeverity, SkillScanResultStatus, SkillScanStatusReason
from skill_scan_worker.adapters import ENABLED_ADAPTERS
from skill_scan_worker.adapters.base import ScannerAdapter, ToolOutcome
from skill_scan_worker.config import settings
from skill_scan_worker.process import run_tool_process

_HIGH_SEVERITIES = frozenset({ScanSeverity.HIGH, ScanSeverity.CRITICAL})
_VERSION_TIMEOUT_SECONDS = 30


def extract_version(output: str) -> str | None:
    """The reported version in ``output``: its last whitespace token minus a leading ``v``, or None if empty.

    Cisco prints ``skill-scanner 2.3.2``; SkillSpector prints ``SkillSpector v2.13.3``.
    """
    tokens = output.split()
    if not tokens:
        return None
    return tokens[-1].removeprefix("v")


def _adapter_binary(adapter: ScannerAdapter) -> str:
    """The adapter's own tool binary, via its invocation (so ``--check-versions`` can't drift from it)."""
    invocation = adapter.build_invocation(Path("/nonexistent"), Path("/nonexistent/out"), None)
    return invocation.argv[0]


def check_versions() -> int:
    """Exit non-zero if any adapter's installed tool version differs from its pinned ``tool_version``."""
    failed = False
    for adapter in ENABLED_ADAPTERS:
        binary = _adapter_binary(adapter)
        try:
            process = subprocess.run(  # noqa: S603  # nosec B603 — binary is the adapter's own, not user input
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            print(f"FAIL {adapter.name}: binary not found: {binary}", file=sys.stderr)
            failed = True
            continue
        except subprocess.TimeoutExpired:
            print(f"FAIL {adapter.name}: `{binary} --version` timed out", file=sys.stderr)
            failed = True
            continue
        if process.returncode != 0:
            print(f"FAIL {adapter.name}: `{binary} --version` exited {process.returncode}", file=sys.stderr)
            failed = True
            continue
        reported = extract_version(process.stdout.strip() or process.stderr.strip())
        if reported != adapter.tool_version:
            failed = True
            print(
                f"FAIL {adapter.name}: version {reported!r} != pinned {adapter.tool_version!r} "
                f"(from {process.stdout.strip() or process.stderr.strip()!r})",
                file=sys.stderr,
            )
        else:
            print(f"OK   {adapter.name}: {adapter.tool_version}")
    return 1 if failed else 0


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
        if finding.filePath is not None and not (skill_dir / finding.filePath).exists():
            problems.append(f"finding {finding.ruleId} filePath {finding.filePath} not under skill dir")
    return problems


async def _scan(adapter: ScannerAdapter, skill_dir: Path) -> ToolOutcome:
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
    parser = argparse.ArgumentParser(prog="python -m skill_scan_worker.smoke")
    parser.add_argument(
        "--check-versions", action="store_true", help="assert each tool's --version matches its adapter"
    )
    parser.add_argument("skill_dir", nargs="?", help="directory to scan with every enabled adapter")
    args = parser.parse_args(argv)

    if args.check_versions:
        return check_versions()
    if args.skill_dir is None:
        parser.error("skill_dir is required unless --check-versions is given")
    skill_dir = Path(args.skill_dir)
    if not skill_dir.is_dir():
        print(f"not a directory: {skill_dir}", file=sys.stderr)
        return 2
    return asyncio.run(_run(skill_dir))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
