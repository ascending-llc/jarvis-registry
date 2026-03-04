from dataclasses import dataclass

from httpx import AsyncClient


@dataclass
class McpAppContext:
    """MCP application context with typed dependencies."""

    proxy_client: AsyncClient
