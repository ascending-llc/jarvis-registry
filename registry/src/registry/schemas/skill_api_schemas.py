"""Request and response schemas for the Skill management API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..models.skill_frontmatter import SKILL_NAME_PATTERN
from .acl_schema import ResourcePermissions


class SkillCreateRequest(BaseModel):
    name: str = Field(..., max_length=64, pattern=SKILL_NAME_PATTERN)
    displayTitle: str | None = Field(default=None, max_length=128)
    description: str = Field(..., max_length=1024)
    body: str = Field(default="", max_length=100_000)
    category: str = Field(default="", max_length=128)
    tags: list[str] = Field(default_factory=list)
    alwaysApply: bool = False
    frontmatter: dict[str, Any] = Field(default_factory=dict)


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
