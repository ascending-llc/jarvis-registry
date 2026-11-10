"""Record skill-scan-worker scanner report fixtures from the real pinned tools.

This is the documented step for every scanner version bump: it runs each enabled adapter through the real
``run_tool_process`` against the fixture skills and writes the JSON reports the unit tests replay to
``skill-scan-worker/tests/fixtures/<tool>/``. It must run inside the built skill-scan-worker image, where
the tools are installed, with the repo mounted. From the repo root:

    uv run poe record-skill-scan-fixtures <image> [--with-llm]

Always writes ``clean-nollm.json`` and ``malicious-nollm.json``. With ``--with-llm`` it also writes
``malicious-llm.json`` (a real LLM run) and ``llm-failed.json`` (a run with a deliberately invalid model
id, so the tool's own LLM-failure markers appear). A run that produces no report has its stderr saved
next to the target, which captures the forced-error cases.

The LLM is a Bedrock model read from the environment (the poe task forwards these and the AWS
credential variables into the container):

    SCAN_RECORD_BEDROCK_MODEL       model id or application-inference-profile ARN (required with --with-llm)
    SCAN_RECORD_BEDROCK_BASE_MODEL  the underlying model id (defaults to SCAN_RECORD_BEDROCK_MODEL); for an
                                    ARN, set it to the Anthropic model id, or SkillSpector runs with --no-llm
    AWS_REGION / AWS_DEFAULT_REGION

An LLM fixture is never written from a run in which the adapter disabled the LLM: the script refuses to
start instead.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from beanie import PydanticObjectId
from skill_scan_worker.adapters import ENABLED_ADAPTERS
from skill_scan_worker.adapters.base import ScannerAdapter
from skill_scan_worker.config import settings
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import run_tool_process

from registry_pkgs.models.enums import ModelSourceProviderType
from registry_pkgs.models.skill_scan_result import ScanLLMInfo

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO_ROOT / "skill-scan-worker" / "tests" / "fixtures"
_SKILLS = _FIXTURES / "skills"
_INVALID_MODEL = "does-not-exist.invalid-model-v0"  # a plain id the provider rejects, to force an LLM failure
_FIXTURE_DIRS = {"cisco-skill-scanner": "cisco", "skillspector": "skillspector"}
# Fixtures are committed to a public repo. LLM reports echo the model ARN, and provider errors can echo the
# caller's assumed-role session, so both are redacted to AWS's documented example values.
_ARN_ACCOUNT = re.compile(r"(arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:)\d{12}(?=:)")
_ASSUMED_ROLE_SESSION = re.compile(r"assumed-role/[^/\s\"']+/[^\s\"']+")


def _redact(text: str) -> str:
    """Replace AWS account ids in ARNs and assumed-role session names with placeholders."""
    text = _ARN_ACCOUNT.sub(r"\g<1>123456789012", text)
    return _ASSUMED_ROLE_SESSION.sub("assumed-role/example-role/example-session", text)


def _bedrock_llm(model: str, base_model: str) -> ResolvedLLM:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("--with-llm needs AWS_REGION (or AWS_DEFAULT_REGION) set")
    return ResolvedLLM(
        info=ScanLLMInfo(
            modelSourceId=PydanticObjectId(), providerType=ModelSourceProviderType.AWS_BEDROCK, modelId=model
        ),
        provider=ModelSourceProviderType.AWS_BEDROCK,
        base_model_id=base_model,
        aws_region=region,
        bedrock_model=model,
    )


def _record_llm() -> ResolvedLLM:
    model = os.environ.get("SCAN_RECORD_BEDROCK_MODEL")
    if not model:
        raise SystemExit("--with-llm needs SCAN_RECORD_BEDROCK_MODEL set to a real Bedrock model id or ARN")
    return _bedrock_llm(model, os.environ.get("SCAN_RECORD_BEDROCK_BASE_MODEL") or model)


def _require_llm_usable(llm_targets: list[tuple[ScannerAdapter, ResolvedLLM]]) -> None:
    """Refuse to record an LLM fixture from a run the adapter would make without the LLM."""
    unusable = [
        f"{adapter.name} (model {llm.bedrock_model}, base model {llm.base_model_id})"
        for adapter, llm in llm_targets
        if not adapter.build_invocation(_SKILLS / "malicious", Path(os.devnull), llm).llm_used
    ]
    if unusable:
        raise SystemExit(
            "These adapters would run without the LLM, so their LLM fixtures would be static-only: "
            + "; ".join(unusable)
            + ". For an application-inference-profile ARN, set SCAN_RECORD_BEDROCK_BASE_MODEL."
        )


async def _record(adapter: ScannerAdapter, skill_dir: Path, out_path: Path, llm: ResolvedLLM | None) -> None:
    run_dir = Path(tempfile.mkdtemp(dir=settings.work_dir))
    try:
        output_file = run_dir / f"{adapter.name}.out"
        invocation = adapter.build_invocation(skill_dir, output_file, llm)
        process = await run_tool_process(
            invocation,
            cwd=run_dir,
            timeout=settings.tool_timeout_seconds,
            max_output_bytes=settings.max_output_bytes,
        )
        if output_file.exists() and output_file.read_bytes().strip():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_redact(output_file.read_text()))
            print(f"wrote {out_path.relative_to(_REPO_ROOT)} (exit {process.exit_code})")
        else:
            stderr_path = out_path.parent / f"{out_path.stem}.stderr.txt"
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_path.write_text(_redact(process.stderr_tail))
            print(f"no report for {out_path.name} (exit {process.exit_code}); stderr saved to {stderr_path.name}")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


async def _main(with_llm: bool) -> None:
    llm = _record_llm() if with_llm else None
    invalid_llm = _bedrock_llm(_INVALID_MODEL, _INVALID_MODEL) if with_llm else None
    if llm is not None and invalid_llm is not None:
        _require_llm_usable([(a, m) for a in ENABLED_ADAPTERS for m in (llm, invalid_llm)])
    for adapter in ENABLED_ADAPTERS:
        print(f"== {adapter.name} {adapter.tool_version} ==")
        out_dir = _FIXTURES / _FIXTURE_DIRS.get(adapter.name, adapter.name)
        await _record(adapter, _SKILLS / "clean", out_dir / "clean-nollm.json", None)
        await _record(adapter, _SKILLS / "malicious", out_dir / "malicious-nollm.json", None)
        # Preserve the no-report error fixture alongside the upstream recorder's LLM checks.
        await _record(adapter, _SKILLS / "does-not-exist", out_dir / "forced-error.json", None)
        if llm is not None and invalid_llm is not None:
            await _record(adapter, _SKILLS / "malicious", out_dir / "malicious-llm.json", llm)
            await _record(adapter, _SKILLS / "malicious", out_dir / "llm-failed.json", invalid_llm)


if __name__ == "__main__":
    asyncio.run(_main(with_llm="--with-llm" in sys.argv[1:]))
