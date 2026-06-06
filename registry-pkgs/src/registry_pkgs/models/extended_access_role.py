from enum import StrEnum

from pydantic import Field

from ._generated import AccessRole


class ExtendedAccessRoleResourceType(StrEnum):
    MCP_SERVER = "mcpServer"
    REMOTE_AGENT = "remoteAgent"
    FEDERATION = "federation"
    WORKFLOW = "workflow"


class ExtendedAccessRole(AccessRole):
    resourceType: ExtendedAccessRoleResourceType = Field(...)
