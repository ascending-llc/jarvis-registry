"""Request and response schemas for the Skill management API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..models.skill_frontmatter import SKILL_NAME_PATTERN
from .acl_schema import ResourcePermissions


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


class SkillFileInput(BaseModel):
    """A supporting file supplied inline when creating a skill."""

    relativePath: str = Field(..., max_length=512)
    content: str | None = Field(default=None, description="Text file content (utf-8 string)")
    body: str | None = Field(default=None, description="Binary file content (base64)")
    mimeType: str | None = Field(default=None, max_length=255)
    isExecutable: bool = False
    isBinary: bool | None = Field(default=None, description="Optional; server verifies against content")

    @model_validator(mode="after")
    def _validate_content(self) -> "SkillFileInput":
        _require_exactly_one_content_field(self)
        return self


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


class SkillMetadataResponse(BaseModel):
    id: str
    name: str
    displayTitle: str | None = None
    description: str
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    path: str
    version: int = 1
    fileCount: int = 0
    alwaysApply: bool = False
    enabled: bool = True
    author: str
    authorName: str
    source: str = "inline"
    sourceMetadata: dict[str, Any] | None = None
    createdByRegistry: bool = False
    permissions: ResourcePermissions | None = None
    updatedAt: datetime | None = None


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
