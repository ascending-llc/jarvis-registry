"""
Federation services for integrating with external registries.

Supports federation with:
- Anthropic MCP Registry
- Workday ASOR (Agent Service Operating Registry)
- AWS AgentCore
- Azure AI Foundry
"""

from .anthropic_client import AnthropicFederationClient
from .asor_client import AsorFederationClient
from .azure_ai_foundry_client import AzureAIFoundryFederationClient
from .azure_ai_foundry_client_provider import AzureAIFoundryClientProvider
from .base_client import BaseFederationClient

__all__ = [
    "AnthropicFederationClient",
    "AsorFederationClient",
    "AzureAIFoundryFederationClient",
    "AzureAIFoundryClientProvider",
    "BaseFederationClient",
]
