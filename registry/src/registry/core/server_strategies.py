"""
Server Behavior Strategy Pattern

Handles server-specific URL and header modifications.
Eliminates hardcoded special-case handling scattered across the codebase.
"""

from abc import ABC, abstractmethod


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

    # Additional strategy checks based on tags can be added here.

    # Default strategy for standard servers
    return DefaultStrategy()
