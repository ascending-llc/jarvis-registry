"""
Beanie ODM Models

Exports all Beanie document classes used by this project. Some of them are auto-generated from schemas of Jarvis Chat,
some of them extend the auto-generated class, some of them are newly written in this project.

Also exports two enum types `PrincipalType` and `ResourceType`, so that other modules don't need to import from `_generated`.
"""

from ._generated import (
    IAccessRole,
    IGroup,
    IUser,
    Key,
    PrincipalType,
    ResourceType,
    Token,
)
from .a2a_agent import A2AAgent
from .extended_acl_entry import ExtendedAclEntry
from .extended_mcp_server import ExtendedMCPServerDocument
from .federation import Federation
from .federation_sync_job import FederationSyncJob

__all__ = [
    "A2AAgent",
    "ExtendedAclEntry",
    "ExtendedMCPServerDocument",
    "Federation",
    "FederationSyncJob",
    "IAccessRole",
    "IGroup",
    "IUser",
    "Key",
    "Token",
    "PrincipalType",
    "ResourceType",
]
