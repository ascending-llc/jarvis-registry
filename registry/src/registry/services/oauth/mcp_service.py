import asyncio
import logging

from .connection_service import get_connection_service
from .oauth_service import get_oauth_service

logger = logging.getLogger(__name__)


class MCPService:
    """MCP main service"""

    def __init__(self):
        self.connection_service = None
        self.oauth_service = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Initialize all services

        Raises:
            Exception: If any service initialization fails
        """
        async with self._lock:
            if self._initialized:
                logger.debug("MCP service already initialized")
                return

            try:
                logger.info("Initializing MCP service components...")

                # Initialize connection service
                self.connection_service = await get_connection_service()
                logger.debug("Connection service initialized")

                # Initialize OAuth service
                self.oauth_service = await get_oauth_service()
                logger.debug("OAuth service initialized")

                # Mark as initialized
                self._initialized = True
                logger.info("MCP service initialized successfully")

            except Exception as e:
                logger.error(f"Failed to initialize MCP service: {e}", exc_info=True)
                # Reset state on failure
                self.connection_service = None
                self.oauth_service = None
                self._initialized = False
                raise RuntimeError(f"MCP service initialization failed: {e}")


# Global MCP service instance
_mcp_service_instance: MCPService | None = None


async def get_mcp_service() -> MCPService:
    global _mcp_service_instance
    if _mcp_service_instance is None:
        _mcp_service_instance = MCPService()
        await _mcp_service_instance.initialize()
    return _mcp_service_instance
