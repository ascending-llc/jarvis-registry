from datetime import datetime
from enum import StrEnum

from beanie import Document
from pydantic import Field


class GroupSource(StrEnum):
    LOCAL = "local"
    ENTRA = "entra"


class Group(Document):
    name: str
    description: str | None = None
    email: str | None = None
    avatar: str | None = None
    memberIds: list[str] = Field(default_factory=list)
    source: GroupSource = GroupSource.LOCAL
    idOnTheSource: str | None = None
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "groups"
        keep_nulls = False
        use_state_management = True
