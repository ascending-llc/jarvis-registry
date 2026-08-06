from datetime import datetime

from beanie import Document, PydanticObjectId


class SkillFile(Document):
    skillId: PydanticObjectId
    relativePath: str
    content: str | None = None
    mimeType: str
    bytes: int
    isBinary: bool = False
    isExecutable: bool = False
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "skillfiles"
        keep_nulls = False
        use_state_management = True
