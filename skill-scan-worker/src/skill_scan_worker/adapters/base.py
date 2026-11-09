"""The ``ScannerAdapter`` protocol and its result dataclass. Concrete adapters are."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from registry_pkgs.models.enums import SkillScanResultStatus, SkillScanStatusReason
from registry_pkgs.models.skill_scan_result import ScanFinding, ScanUsage
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import Invocation, ProcessResult


@dataclass(frozen=True)
class ToolOutcome:
    status: SkillScanResultStatus
    status_reason: SkillScanStatusReason | None
    findings: list[ScanFinding]
    usage: ScanUsage | None = None
    tool_metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


class ScannerAdapter(Protocol):
    name: str  # stable id stored in results, e.g. "cisco-skill-scanner"
    tool_version: str  # exact pinned version, must equal the version installed in the image
    llm_input_unbounded: bool  # True if the tool sends all content to the LLM with no budget of its own

    def static_fingerprint(self) -> str: ...

    def build_invocation(self, skill_dir: Path, output_file: Path, llm: ResolvedLLM | None) -> Invocation: ...

    def parse(self, process: ProcessResult, *, llm_requested: bool) -> ToolOutcome: ...


def static_fingerprint_for(name: str, version: str, config: Mapping[str, Any]) -> str:
    """A stable fingerprint over an adapter's identity and every output-affecting flag (never secrets)."""
    raw = json.dumps(
        {"name": name, "version": version, "config": dict(config)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
