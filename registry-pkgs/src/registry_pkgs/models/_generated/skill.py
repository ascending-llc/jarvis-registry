from datetime import datetime
from enum import StrEnum
from typing import Any

from beanie import Document, PydanticObjectId
from pydantic import Field


class SkillSource(StrEnum):
    INLINE = "inline"
    GITHUB = "github"
    NOTION = "notion"


class Skill(Document):
    name: str
    displayTitle: str | None = None
    description: str
    body: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    disableModelInvocation: bool = False
    userInvocable: bool = True
    allowedTools: list[str] | None = None
    category: str = ""
    author: PydanticObjectId
    authorName: str
    version: int = 1
    source: SkillSource = SkillSource.INLINE
    sourceMetadata: dict[str, Any] | None = None
    fileCount: int = 0
    alwaysApply: bool = False
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "skills"
        keep_nulls = False
        use_state_management = True
