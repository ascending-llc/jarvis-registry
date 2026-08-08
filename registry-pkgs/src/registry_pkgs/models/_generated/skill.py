from datetime import datetime
from typing import Any

from beanie import Document, PydanticObjectId
from pydantic import Field


class Skill(Document):
    name: str
    description: str
    body: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    disableModelInvocation: bool = False
    userInvocable: bool = True
    allowedTools: list[str] | None = None
    author: PydanticObjectId
    version: int = 1
    fileCount: int = 0
    alwaysApply: bool = False
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "skills"
        keep_nulls = False
        use_state_management = True
