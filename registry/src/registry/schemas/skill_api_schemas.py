"""Request and response schemas for the Skill management API."""

# ruff: noqa: UP045 -- Repository guidance requires explicit Optional[T] annotations.

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .acl_schema import ResourcePermissions


class SkillCreateRequest(BaseModel):
    name: str = Field(..., max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    displayTitle: Optional[str] = Field(default=None, max_length=128)
    description: str = Field(..., max_length=1024)
    body: str = Field(default="", max_length=100_000)
    category: str = Field(default="", max_length=128)
    tags: list[str] = Field(default_factory=list)
    alwaysApply: bool = False
    userInvocable: bool = True
    disableModelInvocation: bool = False
    allowedTools: Optional[list[str]] = None


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    displayTitle: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=1024)
    body: Optional[str] = Field(default=None, max_length=100_000)
    category: Optional[str] = Field(default=None, max_length=128)
    tags: Optional[list[str]] = None
    alwaysApply: Optional[bool] = None
    userInvocable: Optional[bool] = None
    disableModelInvocation: Optional[bool] = None
    allowedTools: Optional[list[str]] = None


class SkillToggleRequest(BaseModel):
    enabled: bool


class SkillFileMetadataResponse(BaseModel):
    id: str
    relativePath: str
    mimeType: str
    bytes: int
    isBinary: Optional[bool] = None
    isExecutable: bool = False
    source: Optional[str] = None


class SkillFileResponse(BaseModel):
    """File representation retained by the CLI sync-down response."""

    relativePath: str
    content: Optional[str] = None
    mimeType: str
    bytes: int
    isBinary: Optional[bool] = None
    isExecutable: bool = False
    source: Optional[str] = None
    available: bool = True
    unavailableReason: Optional[str] = None


class SkillMetadataResponse(BaseModel):
    id: str
    name: str
    displayTitle: Optional[str] = None
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
    sourceMetadata: Optional[dict[str, Any]] = None
    permissions: Optional[ResourcePermissions] = None
    updatedAt: Optional[datetime] = None
    deletedAt: Optional[datetime] = None


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
    allowedTools: Optional[list[str]] = None
    category: str = ""
    files: list[SkillFileResponse]


class SkillDetailResponse(BaseModel):
    id: str
    name: str
    displayTitle: Optional[str] = None
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
    allowedTools: Optional[list[str]] = None
    author: str
    authorName: str
    source: str = "inline"
    sourceMetadata: Optional[dict[str, Any]] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None
    files: list[SkillFileMetadataResponse] = Field(default_factory=list)
    permissions: Optional[ResourcePermissions] = None


class SkillFileContentResponse(BaseModel):
    relativePath: str
    content: Optional[str] = None
    body: Optional[str] = Field(default=None, description="Base64-encoded binary content")
    mimeType: str
    isBinary: Optional[bool] = None
    available: bool = True
    unavailableReason: Optional[str] = None


class SkillToggleResponse(BaseModel):
    id: str
    enabled: bool
