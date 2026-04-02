"""
Extended ACL Entry Model for Registry-Specific Flexibility

This module extends the auto-generated IAclEntry with a `resourceType` field that accepts one more Enum value.
The base model (_generated/aclEntry.py) should NOT be modified as it's auto-generated.
"""

from enum import StrEnum

from pydantic import Field

from ._generated.aclEntry import IAclEntry


class ExtendedResourceType(StrEnum):
    AGENT = "agent"
    PROMPTGROUP = "promptGroup"
    MCPSERVER = "mcpServer"
    REMOTE_AGENT = "remoteAgent"
    FEDERATION = "federation"


class ExtendedAclEntry(IAclEntry):
    """
    Extended ACL Entry Document
    """

    resourceType: ExtendedResourceType = Field(...)  # type: ignore[assignment]
