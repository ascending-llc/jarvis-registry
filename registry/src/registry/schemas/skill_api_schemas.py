"""Request and response schemas for the Skill management API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from registry_pkgs.models.enums import (
    ModelSourceProviderType,
    ScanFindingCategory,
    ScanSeverity,
    SkillScanJobStatus,
    SkillScanResultStatus,
    SkillScanStatusReason,
    SkillScanTriggerType,
)

from ..constants import MAX_SKILL_FILE_RELATIVE_PATH_LENGTH
from ..models.skill_frontmatter import SKILL_NAME_PATTERN
from .acl_schema import ResourcePermissions

# API-facing scan status. Maps the job's own status: no job -> "not_scanned",
# SkillScanJobStatus.DONE -> "completed"; pending/scanning/failed pass through by value.
SkillScanStatus = Literal["not_scanned", "pending", "scanning", "completed", "failed"]


def _require_exactly_one_content_field(model: BaseModel) -> None:
    if (model.content is None) == (model.body is None):  # type: ignore[attr-defined]
        raise ValueError("Exactly one of 'content' or 'body' must be provided")


class SkillFileUpsertRequest(BaseModel):
    """Body for creating/replacing a single supporting file (relativePath comes from the URL)."""

    content: str | None = Field(default=None, description="Text file content (utf-8 string)")
    body: str | None = Field(default=None, description="Binary file content (base64)")
    mimeType: str | None = Field(default=None, max_length=255)
    isExecutable: bool = False
    isBinary: bool | None = Field(default=None, description="Optional; server verifies against content")

    @model_validator(mode="after")
    def _validate_content(self) -> "SkillFileUpsertRequest":
        _require_exactly_one_content_field(self)
        return self


class SkillFileInput(SkillFileUpsertRequest):
    """A supporting file supplied inline when creating a skill."""

    relativePath: str = Field(..., max_length=MAX_SKILL_FILE_RELATIVE_PATH_LENGTH)


class SkillCreateRequest(BaseModel):
    name: str = Field(..., max_length=64, pattern=SKILL_NAME_PATTERN)
    displayTitle: str | None = Field(default=None, max_length=128)
    description: str = Field(..., max_length=1024)
    body: str = Field(default="", max_length=100_000)
    category: str = Field(default="", max_length=128)
    tags: list[str] = Field(default_factory=list)
    alwaysApply: bool = False
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    files: list[SkillFileInput] = Field(default_factory=list)


class SkillUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64, pattern=SKILL_NAME_PATTERN)
    displayTitle: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    body: str | None = Field(default=None, max_length=100_000)
    category: str | None = Field(default=None, max_length=128)
    tags: list[str] | None = None
    alwaysApply: bool | None = None
    frontmatter: dict[str, Any] | None = None


class SkillToggleRequest(BaseModel):
    enabled: bool


class SkillFileMetadataResponse(BaseModel):
    id: str
    relativePath: str
    mimeType: str
    bytes: int
    isBinary: bool | None = None
    isExecutable: bool = False
    source: str | None = None


class SkillFileResponse(BaseModel):
    """File representation retained by the CLI sync-down response."""

    relativePath: str
    content: str | None = None
    body: str | None = Field(default=None, description="Base64-encoded binary content")
    mimeType: str
    bytes: int
    isBinary: bool | None = None
    isExecutable: bool = False
    source: str | None = None
    available: bool = True
    unavailableReason: str | None = None


class SkillOriginProviderResponse(BaseModel):
    id: str  # sourceMetadata.sourceId, as stored
    name: str | None = None  # SkillSyncSource.displayName; None when the provider can't be resolved
    type: str  # skill.source, e.g. "github"
    repo: str | None = None  # f"{owner}/{repo}" from sourceMetadata; None if either is missing
    ref: str | None = None  # sourceMetadata.ref


class SkillOriginResponse(BaseModel):
    kind: Literal["registry", "chat", "external_provider"]
    provider: SkillOriginProviderResponse | None = None  # set iff kind == "external_provider"


class SkillScanJobResponse(BaseModel):
    status: SkillScanJobStatus
    rerequested: bool
    triggerType: SkillScanTriggerType
    requestedAt: datetime
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    attemptCount: int
    errorCode: str | None = None


class SkillScanFindingResponse(BaseModel):
    tool: str
    ruleId: str | None = None
    severity: ScanSeverity
    category: ScanFindingCategory
    toolCategory: str | None = None
    title: str
    message: str
    filePath: str | None = None
    startLine: int | None = None
    endLine: int | None = None
    snippet: str | None = None
    remediation: str | None = None
    confidence: float | None = None


class SkillScanToolResultResponse(BaseModel):
    tool: str
    toolVersion: str
    status: SkillScanResultStatus
    statusReason: SkillScanStatusReason | None = None
    outdatedTool: bool
    findings: list[SkillScanFindingResponse] = Field(default_factory=list)
    findingsTruncated: bool = False
    llmProviderType: ModelSourceProviderType | None = None
    llmModelId: str | None = None
    inputTokens: int | None = None
    outputTokens: int | None = None
    durationMs: int | None = None
    errorMessage: str | None = None
    toolMetadata: dict[str, Any] = Field(default_factory=dict)
    scannedAt: datetime  # SkillScanResult.updatedAt


class SkillScanSummaryResponse(BaseModel):
    contentDigest: str
    skillVersion: int
    status: SkillScanResultStatus
    maxSeverity: ScanSeverity | None = None
    severityCounts: dict[ScanSeverity, int] = Field(default_factory=dict)
    scannedAt: datetime


class SkillScanResponse(BaseModel):
    skillId: str
    skillVersion: int  # current skills.version
    scanStatus: SkillScanStatus
    stale: bool  # summary is not None and summary.skillVersion != skillVersion
    outdatedTool: bool  # any result's outdatedTool
    job: SkillScanJobResponse | None = None
    summary: SkillScanSummaryResponse | None = None
    results: list[SkillScanToolResultResponse] = Field(default_factory=list)  # ordered by tool name


class SkillScanBriefResponse(BaseModel):
    scanStatus: SkillScanStatus
    status: SkillScanResultStatus | None = None
    maxSeverity: ScanSeverity | None = None
    severityCounts: dict[ScanSeverity, int] = Field(default_factory=dict)
    stale: bool = False
    outdatedTool: bool = False
    scannedAt: datetime | None = None


class SkillMetadataResponse(BaseModel):
    id: str
    name: str
    displayTitle: str | None = None
    description: str
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    fileCount: int = 0
    alwaysApply: bool = False
    enabled: bool = True
    author: str
    authorName: str
    source: str = "inline"
    sourceMetadata: dict[str, Any] | None = None
    createdByRegistry: bool = False
    origin: SkillOriginResponse
    permissions: ResourcePermissions | None = None
    updatedAt: datetime | None = None
    scanSummary: SkillScanBriefResponse | None = None


class SkillListResponse(BaseModel):
    skills: list[SkillMetadataResponse]


class SkillContentResponse(BaseModel):
    id: str
    name: str
    description: str
    body: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    alwaysApply: bool = False
    disableModelInvocation: bool = False
    userInvocable: bool = True
    allowedTools: list[str] | None = None
    category: str = ""
    createdByRegistry: bool = False
    files: list[SkillFileResponse]


class SkillDetailResponse(BaseModel):
    id: str
    name: str
    displayTitle: str | None = None
    description: str
    body: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    fileCount: int = 0
    enabled: bool = True
    alwaysApply: bool = False
    userInvocable: bool = True
    disableModelInvocation: bool = False
    allowedTools: list[str] | None = None
    author: str
    authorName: str
    source: str = "inline"
    sourceMetadata: dict[str, Any] | None = None
    createdByRegistry: bool = False
    origin: SkillOriginResponse
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    files: list[SkillFileMetadataResponse] = Field(default_factory=list)
    permissions: ResourcePermissions | None = None


class SkillFileContentResponse(BaseModel):
    relativePath: str
    content: str | None = None
    body: str | None = Field(default=None, description="Base64-encoded binary content")
    mimeType: str
    isBinary: bool | None = None
    available: bool = True
    unavailableReason: str | None = None


class SkillToggleResponse(BaseModel):
    id: str
    enabled: bool
