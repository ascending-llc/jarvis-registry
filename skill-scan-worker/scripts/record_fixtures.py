"""Record scanner report fixtures from the real pinned tools.

This is the documented step for every version bump: it runs each enabled adapter through the real
``run_tool_process`` against the fixture skills and writes the JSON reports the unit tests replay.
Run it inside the built image, where the tools are installed:

    docker run --rm -v "$PWD/skill-scan-worker:/w" -w /w <image> \
        python scripts/record_fixtures.py [--with-llm]

Always writes ``clean-nollm.json`` and ``malicious-nollm.json``. With ``--with-llm`` it also writes
``malicious-llm.json`` (a real LLM run) and ``llm-failed.json`` (a run with a deliberately invalid model
id, so the tool's own LLM-failure markers appear). A run that produces no report has its stderr saved
next to the target, which captures the forced-error cases.

The LLM is a Bedrock model read from the environment, so no code edit is needed:

    SCAN_RECORD_BEDROCK_MODEL=<model-id-or-arn> AWS_REGION=<region> \
        python scripts/record_fixtures.py --with-llm
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

from beanie import PydanticObjectId
from skill_scan_worker.adapters import ENABLED_ADAPTERS
from skill_scan_worker.config import settings
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import run_tool_process

from registry_pkgs.models.enums import ModelSourceProviderType
from registry_pkgs.models.skill_scan_result import ScanLLMInfo

_ROOT = Path(__file__).resolve().parent.parent
_SKILLS = _ROOT / "tests" / "fixtures" / "skills"
_FIXTURES = _ROOT / "tests" / "fixtures"
_INVALID_MODEL = "does-not-exist.invalid-model-v0"  # a plain id the provider rejects, to force an LLM failure


def _bedrock_llm(model: str) -> ResolvedLLM:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("--with-llm needs AWS_REGION (or AWS_DEFAULT_REGION) set")
    return ResolvedLLM(
        info=ScanLLMInfo(
            modelSourceId=PydanticObjectId(), providerType=ModelSourceProviderType.AWS_BEDROCK, modelId=model
        ),
        provider=ModelSourceProviderType.AWS_BEDROCK,
        base_model_id=model,
        aws_region=region,
        bedrock_model=model,
    )


def _record_llm() -> ResolvedLLM:
    model = os.environ.get("SCAN_RECORD_BEDROCK_MODEL")
    if not model:
        raise SystemExit("--with-llm needs SCAN_RECORD_BEDROCK_MODEL set to a real Bedrock model id or ARN")
    return _bedrock_llm(model)


async def _record(adapter, skill_dir: Path, out_path: Path, llm: ResolvedLLM | None) -> None:
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
            shutil.copyfile(output_file, out_path)
            print(f"wrote {out_path.relative_to(_ROOT)} (exit {process.exit_code})")
        else:
            stderr_path = out_path.parent / f"{out_path.stem}.stderr.txt"
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_path.write_text(process.stderr_tail)
            print(f"no report for {out_path.name} (exit {process.exit_code}); stderr saved to {stderr_path.name}")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


async def _main(with_llm: bool) -> None:
    llm = _record_llm() if with_llm else None
    for adapter in ENABLED_ADAPTERS:
        print(f"== {adapter.name} {adapter.tool_version} ==")
        out_dir = _FIXTURES / adapter.name.replace("-skill-scanner", "")
        await _record(adapter, _SKILLS / "clean", out_dir / "clean-nollm.json", None)
        await _record(adapter, _SKILLS / "malicious", out_dir / "malicious-nollm.json", None)
        if with_llm:
            await _record(adapter, _SKILLS / "malicious", out_dir / "malicious-llm.json", llm)
            await _record(adapter, _SKILLS / "malicious", out_dir / "llm-failed.json", _bedrock_llm(_INVALID_MODEL))


if __name__ == "__main__":
    asyncio.run(_main(with_llm="--with-llm" in sys.argv[1:]))
