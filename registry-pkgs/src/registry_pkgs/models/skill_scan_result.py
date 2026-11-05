"""One scan result per (skillId, contentDigest, toolFingerprint). Registry-owned collection."""

from datetime import UTC, datetime
from typing import Any

from beanie import Document, Insert, PydanticObjectId, Replace, Save, before_event
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import IndexModel

from .enums import (
    ModelSourceProviderType,
    ScanFindingCategory,
    ScanSeverity,
    SkillScanResultStatus,
    SkillScanStatusReason,
)

# Caps enforced on stored values. `findings` is capped by the scanner worker, which sets findingsTruncated.
MAX_FINDING_SNIPPET_CHARS = 500
MAX_FINDINGS_PER_RESULT = 200
MAX_ERROR_MESSAGE_CHARS = 2000


class ScanFinding(BaseModel):
    tool: str  # adapter name, e.g. "cisco-skill-scanner"
    ruleId: str | None = None
    severity: ScanSeverity
    category: ScanFindingCategory
    toolCategory: str | None = None  # tool-native category, unmapped
    title: str
    message: str
    filePath: str | None = None  # relative to the skill root; "SKILL.md" for the main file
    startLine: int | None = None
    endLine: int | None = None
    snippet: str | None = None  # capped, see MAX_FINDING_SNIPPET_CHARS
    remediation: str | None = None
    confidence: float | None = None  # 0..1 when the tool reports one

    @model_validator(mode="after")
    def _cap_snippet(self) -> "ScanFinding":
        if self.snippet is not None and len(self.snippet) > MAX_FINDING_SNIPPET_CHARS:
            self.snippet = self.snippet[:MAX_FINDING_SNIPPET_CHARS]
        return self


class ScanLLMInfo(BaseModel):
    modelSourceId: PydanticObjectId
    providerType: ModelSourceProviderType
    modelId: str  # modelIdOrArn (Bedrock) or deploymentName (Azure)
    # sha256 hex of the provider fields that decide which deployment serves the model (Bedrock: region and
    # model id; Azure: endpoint, deployment and apiVersion), so an in-place edit that keeps the same source
    # id and model id but points at a different resource still produces a new tool fingerprint. Never
    # covers credentials. See skill_scan.plan.compose_tool_fingerprint.
    configDigest: str | None = None


class ScanUsage(BaseModel):
    inputTokens: int | None = None
    outputTokens: int | None = None


class SkillScanResult(Document):
    skillId: PydanticObjectId
    contentDigest: str  # "sha256:<hex>", see skill_scan.content
    skillVersion: int  # skills.version at scan time (display + staleness only)
    tool: str
    toolVersion: str
    toolFingerprint: str
    status: SkillScanResultStatus
    statusReason: SkillScanStatusReason | None = None
    findings: list[ScanFinding] = Field(default_factory=list)
    findingsTruncated: bool = False
    # The configured scan model, even if this tool ran without it; None when none is configured.
    llm: ScanLLMInfo | None = None
    usage: ScanUsage | None = None
    toolMetadata: dict[str, Any] = Field(default_factory=dict)  # small tool-specific extras, e.g. risk score
    errorMessage: str | None = None  # capped, never contains env or secrets
    durationMs: int | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "skill_scan_results"
        keep_nulls = False
        indexes = [
            IndexModel([("skillId", 1), ("contentDigest", 1), ("toolFingerprint", 1)], unique=True),
            IndexModel([("skillId", 1), ("updatedAt", -1)]),
        ]

    @model_validator(mode="after")
    def _cap_error_message(self) -> "SkillScanResult":
        if self.errorMessage is not None and len(self.errorMessage) > MAX_ERROR_MESSAGE_CHARS:
            self.errorMessage = self.errorMessage[:MAX_ERROR_MESSAGE_CHARS]
        return self

    @before_event(Insert, Replace, Save)
    async def update_timestamps(self) -> None:
        self.updatedAt = datetime.now(UTC)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
