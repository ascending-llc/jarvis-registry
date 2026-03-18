"""
Server Behavior Strategy Pattern

Handles server-specific URL and header modifications.
Eliminates hardcoded special-case handling scattered across the codebase.
"""

import logging
from abc import ABC, abstractmethod

from .mcp_config import mcp_config

logger = logging.getLogger(__name__)


class ServerBehaviorStrategy(ABC):
    """Base strategy for server-specific behavior"""

    @abstractmethod
    def modify_url(self, url: str) -> str:
        """Modify URL for server-specific requirements"""
        pass

    @abstractmethod
    def modify_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Modify headers for server-specific requirements"""
        pass


class DefaultStrategy(ServerBehaviorStrategy):
    """Default behavior for standard MCP servers"""

    def modify_url(self, url: str) -> str:
        """No URL modifications for standard servers"""
        return url

    def modify_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """No header modifications for standard servers"""
        return headers


class AnthropicRegistryStrategy(ServerBehaviorStrategy):
    """Behavior for servers imported from Anthropic registry"""

    def modify_url(self, url: str) -> str:
        """
        Add instance_id parameter for Anthropic registry servers.

        Anthropic registry servers (streamable-http and sse) require
        instance_id=default query parameter.
        """
        if "?" not in url:
            modified_url = f"{url}?{mcp_config.ANTHROPIC_QUERY_PARAM}"
        elif "instance_id=" not in url:
            modified_url = f"{url}&{mcp_config.ANTHROPIC_QUERY_PARAM}"
        else:
            modified_url = url

        if modified_url != url:
            logger.debug(f"Added instance_id parameter for Anthropic server: {url} -> {modified_url}")

        return modified_url

    def modify_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """No special header modifications for Anthropic servers"""
        return headers


def get_server_strategy(server_info: dict) -> ServerBehaviorStrategy:
    """
    Factory function to get appropriate strategy based on server info.

    Args:
        server_info: Server configuration dictionary containing tags and other metadata

    Returns:
        Appropriate ServerBehaviorStrategy instance
    """
    if not server_info:
        return DefaultStrategy()

    tags = server_info.get("tags", [])

    # Check for Anthropic registry servers
    if mcp_config.ANTHROPIC_TAG in tags:
        logger.debug("Using AnthropicRegistryStrategy for server")
        return AnthropicRegistryStrategy()

    # Additional strategy checks based on tags can be added here.

    # Default strategy for standard servers
    return DefaultStrategy()
