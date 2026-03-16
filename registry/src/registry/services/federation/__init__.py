"""
Federation services for integrating with external registries.

Supports federation with:
- Anthropic MCP Registry
- Workday ASOR (Agent Service Operating Registry)
"""

from .anthropic_client import AnthropicFederationClient
from .asor_client import AsorFederationClient
from .base_client import BaseFederationClient

__all__ = [
    "AnthropicFederationClient",
    "AsorFederationClient",
    "BaseFederationClient",
]
