from datetime import datetime
from enum import StrEnum

from beanie import Document


class AccessRoleResourceType(StrEnum):
    AGENT = "agent"
    PROJECT = "project"
    FILE = "file"
    PROMPT_GROUP = "promptGroup"
    MCP_SERVER = "mcpServer"
    REMOTE_AGENT = "remoteAgent"


class AccessRole(Document):
    accessRoleId: str
    name: str
    description: str | None = None
    resourceType: AccessRoleResourceType = AccessRoleResourceType.AGENT
    permBits: int
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "accessroles"
        keep_nulls = False
        use_state_management = True
