from datetime import UTC, datetime
from enum import StrEnum

from beanie import Document, PydanticObjectId
from pydantic import Field


class PrincipalType(StrEnum):
    USER = "user"
    GROUP = "group"
    PUBLIC = "public"
    ROLE = "role"


class PrincipalModel(StrEnum):
    USER = "User"
    GROUP = "Group"
    ROLE = "Role"


class ResourceType(StrEnum):
    AGENT = "agent"
    PROMPTGROUP = "promptGroup"
    MCPSERVER = "mcpServer"
    REMOTE_AGENT = "remoteAgent"


class AclEntry(Document):
    principalType: PrincipalType
    principalId: PydanticObjectId | str | None = None
    principalModel: PrincipalModel | None = None
    resourceType: ResourceType
    resourceId: PydanticObjectId
    permBits: int = 1
    roleId: PydanticObjectId | None = None
    inheritedFrom: PydanticObjectId | None = None
    grantedBy: PydanticObjectId | None = None
    grantedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "aclentries"
        keep_nulls = False
        use_state_management = True
