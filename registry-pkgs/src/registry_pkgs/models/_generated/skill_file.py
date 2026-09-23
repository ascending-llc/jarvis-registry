from datetime import datetime
from typing import Any

from beanie import Document, PydanticObjectId


class SkillFile(Document):
    skillId: PydanticObjectId
    relativePath: str
    source: str
    mimeType: str
    bytes: int
    isExecutable: bool = False
    content: str | None = None
    isBinary: bool | None = None
    codeEnvRefs: dict[str, Any] | None = None
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "skillfiles"
        keep_nulls = False
        use_state_management = True
