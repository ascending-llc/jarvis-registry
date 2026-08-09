from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SkillMetadataResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    path: str
    version: int = 1
    fileCount: int = 0
    alwaysApply: bool = False
    updatedAt: datetime
    deletedAt: datetime | None = None


class SkillListResponse(BaseModel):
    skills: list[SkillMetadataResponse]


class SkillFileResponse(BaseModel):
    relativePath: str
    content: str | None = None
    mimeType: str
    bytes: int
    isBinary: bool | None = None
    isExecutable: bool = False


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
    files: list[SkillFileResponse]
