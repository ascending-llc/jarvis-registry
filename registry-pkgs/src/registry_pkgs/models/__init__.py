"""
Beanie ODM Models

Exports auto-generated models from _generated/ and static models
"""

from .a2a_agent import A2AAgent
from .enums import ToolDiscoveryMode
from .extended_mcp_server import ExtendedMCPServer, MCPServerDocument

# Export auto-generated models from _generated/
try:
    from ._generated import *  # noqa: F403, F401
    from ._generated import __all__ as _generated_all

    __all__ = [
        "A2AAgent",
        "ExtendedMCPServer",
        "MCPServerDocument",
        "ToolDiscoveryMode",
    ] + _generated_all
except ImportError:
    # _generated doesn't exist yet - will be created by import-schemas
    __all__ = [
        "A2AAgent",
        "ExtendedMCPServer",
        "MCPServerDocument",
        "ToolDiscoveryMode",
    ]
