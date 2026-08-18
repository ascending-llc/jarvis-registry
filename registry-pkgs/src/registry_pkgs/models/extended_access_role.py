from enum import StrEnum

from pydantic import Field

from ._generated import AccessRole


class RegistryResourceType(StrEnum):
    MCP_SERVER = "mcpServer"
    REMOTE_AGENT = "remoteAgent"
    FEDERATION = "federation"
    WORKFLOW = "workflow"
    SKILL = "skill"
    SKILL_SYNC_SOURCE = "skillSyncSource"


class RegistryAccessRole(AccessRole):
    resourceType: RegistryResourceType = Field(...)
