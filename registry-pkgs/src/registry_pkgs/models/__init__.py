"""
Beanie ODM Models

Exports all Beanie document classes used by this project. Some of them are auto-generated from schemas of Jarvis Chat,
some of them extend the auto-generated class, some of them are newly written in this project.

Also exports two enum types `PrincipalType` and `ResourceType`, so that other modules don't need to import from `_generated`.
"""

from ._generated import (
    Group,
    Key,
    PrincipalType,
    ResourceType,
    Token,
    User,
)
from .a2a_agent import A2AAgent
from .extended_access_role import RegistryAccessRole
from .extended_acl_entry import RegistryAclEntry
from .extended_mcp_server import ExtendedMCPServer
from .federation import Federation
from .federation_sync_job import FederationSyncJob
from .token_type import TokenType
from .workflow import NodeRun, WorkflowDefinition, WorkflowRun, WorkflowVersion

__all__ = [
    "A2AAgent",
    "RegistryAclEntry",
    "ExtendedMCPServer",
    "Federation",
    "FederationSyncJob",
    "RegistryAccessRole",
    "NodeRun",
    "WorkflowDefinition",
    "WorkflowRun",
    "WorkflowVersion",
    "Group",
    "User",
    "Key",
    "Token",
    "TokenType",
    "PrincipalType",
    "ResourceType",
]
