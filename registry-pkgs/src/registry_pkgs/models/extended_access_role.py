from enum import StrEnum

from ._generated import AccessRole


class ExtendedAccessRoleResourceType(StrEnum):
    AGENT = "agent"
    PROJECT = "project"
    FILE = "file"
    PROMPT_GROUP = "promptGroup"
    MCP_SERVER = "mcpServer"
    REMOTE_AGENT = "remoteAgent"
    FEDERATION = "federation"


class ExtendedAccessRole(AccessRole):
    resourceType: ExtendedAccessRoleResourceType = ExtendedAccessRoleResourceType.AGENT  # type: ignore[assignment]
