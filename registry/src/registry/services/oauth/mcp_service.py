import asyncio
import logging

from registry_pkgs.oauth.oauth_service import MCPOAuthService

from .connection_service import MCPConnectionService

logger = logging.getLogger(__name__)


class MCPService:
    """MCP main service"""

    def __init__(
        self,
        connection_service: MCPConnectionService,
        oauth_service: MCPOAuthService,
    ):
        self.connection_service = connection_service
        self.oauth_service = oauth_service
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
                logger.debug("Connection service initialized")
                logger.debug("OAuth service initialized")

                # Mark as initialized
                self._initialized = True
                logger.info("MCP service initialized successfully")

            except Exception as e:
                logger.error(f"Failed to initialize MCP service: {e}", exc_info=True)
                self._initialized = False
                raise RuntimeError(f"MCP service initialization failed: {e}")
