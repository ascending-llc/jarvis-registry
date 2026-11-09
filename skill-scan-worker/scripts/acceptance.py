"""Acceptance check for the built skill-scan worker image: runs each adapter against the malicious and
clean fixtures (plus the LLM path when SCAN_MODEL is set) and verifies the outcome of every scenario.

Run from the repo root:

    docker compose run --build --rm --no-deps \
        -v "$PWD/skill-scan-worker/tests/fixtures/skills:/fixtures:ro" \
        skill-scan-worker python scripts/acceptance.py

Set SCAN_MODEL to a Bedrock model id/ARN to also run the LLM scenarios (for an ARN, set SCAN_BASE_MODEL to
the underlying anthropic model). Exit is non-zero on any failure; an LLM path the environment blocks (geo
restriction, missing access) is reported BLOCKED, not failed.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from beanie import PydanticObjectId
from skill_scan_worker.adapters import ENABLED_ADAPTERS
from skill_scan_worker.adapters.base import ToolOutcome
from skill_scan_worker.config import settings
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import run_tool_process
from smoke import check_outcome  # baked beside this script in the image; reuses the no-llm contract

from registry_pkgs.models.enums import (
    ModelSourceProviderType,
    ScanSeverity,
    SkillScanResultStatus,
    SkillScanStatusReason,
)
from registry_pkgs.models.skill_scan_result import ScanLLMInfo

_HIGH = frozenset({ScanSeverity.HIGH, ScanSeverity.CRITICAL})
# Statuses that mean the tool's LLM pass did not fully succeed in this environment (geo restriction, access
# denied, throttling, a retired model, partial batch failure): environment-dependent, not a code defect.
_LLM_DEGRADED = frozenset(
    {
        SkillScanStatusReason.LLM_FAILED,
        SkillScanStatusReason.TOOL_ERROR,
        SkillScanStatusReason.ANALYSIS_INCOMPLETE,
    }
)

MALICIOUS = Path(os.environ.get("MALICIOUS_DIR", "/fixtures/malicious"))
CLEAN = Path(os.environ.get("CLEAN_DIR", "/fixtures/clean"))
MODEL = os.environ.get("SCAN_MODEL") or ""
BASE_MODEL = os.environ.get("SCAN_BASE_MODEL") or MODEL
INVALID_MODEL = os.environ.get("SCAN_INVALID_MODEL") or "does-not-exist.invalid-model-v0"

_results: list[tuple[str, str, str]] = []  # (scenario, verdict, detail)


def _record(scenario: str, adapter: str, verdict: str, detail: str) -> None:
    _results.append((scenario, verdict, detail))
    mark = {"PASS": "OK   ", "FAIL": "FAIL ", "BLOCKED": "SKIP "}[verdict]
    print(f"{mark}[{scenario:<18}] {adapter}: {detail}")


def _llm(model: str) -> ResolvedLLM:
    return ResolvedLLM(
        info=ScanLLMInfo(
            modelSourceId=PydanticObjectId(),
            providerType=ModelSourceProviderType.AWS_BEDROCK,
            modelId=model,
        ),
        provider=ModelSourceProviderType.AWS_BEDROCK,
        base_model_id=BASE_MODEL or model,
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        bedrock_model=model,
    )


async def _scan(adapter, skill_dir: Path, llm: ResolvedLLM | None) -> ToolOutcome:
    run_dir = Path(tempfile.mkdtemp(dir=settings.work_dir))
    out = run_dir / f"{adapter.name}.out"
    inv = adapter.build_invocation(skill_dir, out, llm)
    proc = await run_tool_process(
        inv, cwd=run_dir, timeout=settings.tool_timeout_seconds, max_output_bytes=settings.max_output_bytes
    )
    return adapter.parse(proc, llm_requested=inv.llm_used)


async def _run() -> int:
    for adapter in ENABLED_ADAPTERS:
        name = adapter.name

        outcome = await _scan(adapter, MALICIOUS, None)
        problems = check_outcome(name, outcome, MALICIOUS)
        _record(
            "malicious/no-llm",
            name,
            "PASS" if not problems else "FAIL",
            f"{len(outcome.findings)} findings" if not problems else "; ".join(problems),
        )

        outcome = await _scan(adapter, CLEAN, None)
        problems = []
        if (
            outcome.status != SkillScanResultStatus.PARTIAL
            or outcome.status_reason != SkillScanStatusReason.LLM_UNAVAILABLE
        ):
            problems.append(f"status {outcome.status}/{outcome.status_reason} != partial/llm_unavailable")
        if any(f.severity in _HIGH for f in outcome.findings):
            problems.append("unexpected high/critical finding on a clean skill")
        _record(
            "clean/no-llm",
            name,
            "PASS" if not problems else "FAIL",
            f"{len(outcome.findings)} findings" if not problems else "; ".join(problems),
        )

        if not MODEL:
            continue

        outcome = await _scan(adapter, MALICIOUS, _llm(MODEL))
        tokens = (outcome.usage.inputTokens or 0) + (outcome.usage.outputTokens or 0) if outcome.usage else 0
        if outcome.status == SkillScanResultStatus.SUCCEEDED and tokens > 0:
            _record("malicious/llm", name, "PASS", f"succeeded, usage={outcome.usage}")
        elif outcome.status_reason in _LLM_DEGRADED:
            _record(
                "malicious/llm",
                name,
                "BLOCKED",
                f"LLM not fully available here: {outcome.status}/{outcome.status_reason}",
            )
        else:
            _record(
                "malicious/llm",
                name,
                "FAIL",
                f"expected succeeded+usage, got {outcome.status}/{outcome.status_reason} usage={outcome.usage}",
            )

        outcome = await _scan(adapter, MALICIOUS, _llm(INVALID_MODEL))
        ok = outcome.status in (
            SkillScanResultStatus.PARTIAL,
            SkillScanResultStatus.FAILED,
        ) and outcome.status_reason in (
            SkillScanStatusReason.LLM_FAILED,
            SkillScanStatusReason.TOOL_ERROR,
        )
        _record(
            "invalid-model/llm",
            name,
            "PASS" if ok else "FAIL",
            f"{outcome.status}/{outcome.status_reason}"
            if ok
            else f"expected llm_failed/tool_error, got {outcome.status}/{outcome.status_reason}",
        )

    fails = sum(1 for _, v, _ in _results if v == "FAIL")
    blocked = sum(1 for _, v, _ in _results if v == "BLOCKED")
    print(f"\n{len(_results)} checks: {len(_results) - fails - blocked} pass, {fails} fail, {blocked} blocked")
    return 1 if fails else 0


if __name__ == "__main__":
    if not MALICIOUS.is_dir() or not CLEAN.is_dir():
        print(f"fixture dirs not found: {MALICIOUS}, {CLEAN}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_run()))
