"""
Extended ACL Entry Model for Registry-Specific Flexibility

This module extends the auto-generated AclEntry with a `resourceType` field that accepts one more Enum value.
The base model (_generated/acl_entry.py) should NOT be modified as it's auto-generated.
"""

from enum import StrEnum

from pydantic import Field

from ._generated import AclEntry


class ExtendedResourceType(StrEnum):
    AGENT = "agent"
    PROMPTGROUP = "promptGroup"
    MCPSERVER = "mcpServer"
    REMOTE_AGENT = "remoteAgent"
    FEDERATION = "federation"


class ExtendedAclEntry(AclEntry):
    """
    Extended ACL Entry Document
    """

    resourceType: ExtendedResourceType = Field(...)  # type: ignore[assignment]
