"""
Registry ACL Entry Model for Registry-Specific Flexibility

This module extends the auto-generated AclEntry with a `resourceType` field narrowed to the
resource types this Registry system owns (`RegistryResourceType`). The base model
(_generated/acl_entry.py) should NOT be modified as it's auto-generated.
"""

from pydantic import Field

from ._generated import AclEntry
from .extended_access_role import RegistryResourceType


class RegistryAclEntry(AclEntry):
    """
    Registry ACL Entry Document
    """

    resourceType: RegistryResourceType = Field(...)  # type: ignore[assignment]
