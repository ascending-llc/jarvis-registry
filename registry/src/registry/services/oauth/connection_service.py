import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ...schemas.enums import ConnectionState
from ..server_service import server_service_v1
from .base import Connection, ConnectionManager

logger = logging.getLogger(__name__)


@dataclass
class MCPConnection(Connection):
    """MCP connection"""

    def is_stale(self, max_idle_time: float | None = 900) -> bool:
        """
        Check if connection is stale based on idle time.

        Args:
            max_idle_time: Maximum idle time in seconds. If None, defaults to 3600 (1 hour).
                          Should be passed from server config's timeout field.

        A connection is stale if it hasn't been active for longer than max_idle_time.
        """
        current_time = time.time()
        idle_time = current_time - self.last_activity

        return idle_time > max_idle_time


class MCPConnectionService(ConnectionManager):
    """MCP connection service"""

    def __init__(self):
        self.app_connections: dict[str, MCPConnection] = {}
        self.user_connections: dict[str, dict[str, MCPConnection]] = {}  # user_id -> {server_id -> connection}
        self._lock = asyncio.Lock()
        self._max_error_count = 3  # Maximum error count

    async def initialize_app_connections(self) -> None:
        """
        Initialize application-level connections
        """
        async with self._lock:
            try:
                servers, total = await server_service_v1.list_servers(page=1, per_page=1000, status="active")
                logger.info(f"Found {total} active servers in MongoDB")

                for server in servers:
                    if not server.config.get("requiresOAuth"):
                        self.app_connections[server.serverName] = MCPConnection(
                            server_id=str(server.id),
                            connection_state=ConnectionState.CONNECTED,
                            details={
                                "type": "app_connection",
                                "server_id": str(server.id),
                                "url": server.config.get("url"),
                                "config": server.config,
                                "created_at": time.time(),
                                "last_health_check": time.time(),
                            },
                        )
                        logger.debug(f"Created app connection for non-OAuth server: {server.serverName}")
                    else:
                        logger.debug(f"Skipped OAuth server: {server.serverName}")

                logger.info(f"Initialized {len(self.app_connections)} app-level connections from MongoDB")

            except Exception as e:
                logger.error(f"Failed to initialize app connections: {e}", exc_info=True)

    def get_user_connections(self, user_id: str) -> dict[str, MCPConnection]:
        """Get user connections"""
        return self.user_connections.get(user_id, {})

    async def get_connection(self, user_id: str, server_id: str) -> MCPConnection | None:
        """Get connection (application-level or user-level)"""
        # First check application-level connections
        if server_id in self.app_connections:
            return self.app_connections[server_id]

        # Then check user-level connections
        user_conns = self.get_user_connections(user_id)
        if server_id in user_conns:
            return user_conns[server_id]

        return None

    async def update_connection_state(
        self, user_id: str, server_id: str, state: ConnectionState, details: dict[str, Any] | None = None
    ) -> None:
        """Update connection state"""
        async with self._lock:
            connection = await self.get_connection(user_id, server_id)
            if connection:
                connection.connection_state = state
                connection.last_activity = time.time()
                if details:
                    connection.details.update(details)

                if state == ConnectionState.ERROR:
                    connection.error_count += 1

                    # If error count exceeds threshold, mark as disconnected
                    if connection.error_count >= self._max_error_count:
                        connection.connection_state = ConnectionState.DISCONNECTED
                        logger.warning(
                            f"Connection {server_id} for user {user_id} "
                            f"disconnected due to {connection.error_count} errors"
                        )
                elif state == ConnectionState.CONNECTED:
                    connection.error_count = 0

    async def create_user_connection(
        self,
        user_id: str,
        server_id: str,
        initial_state: ConnectionState = ConnectionState.CONNECTING,
        details: dict[str, Any] | None = None,
    ) -> MCPConnection:
        """Create user connection"""
        async with self._lock:
            if user_id not in self.user_connections:
                self.user_connections[user_id] = {}

            connection = MCPConnection(server_id=server_id, connection_state=initial_state, details=details or {})
            self.user_connections[user_id][server_id] = connection
            logger.info(f"Created user connection: {user_id}/{server_id}")
            return connection

    async def disconnect_user_connection(self, user_id: str, server_id: str) -> bool:
        """Disconnect user connection"""
        async with self._lock:
            if user_id in self.user_connections and server_id in self.user_connections[user_id]:
                del self.user_connections[user_id][server_id]
                logger.info(f"Disconnected user connection: {user_id}/{server_id}")

                # If user has no other connections, cleanup user entry
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]

                return True
            return False

    async def disconnect_all_user_connections(self, user_id: str) -> int:
        """Disconnect all user connections"""
        async with self._lock:
            if user_id in self.user_connections:
                count = len(self.user_connections[user_id])
                del self.user_connections[user_id]
                logger.info(f"Disconnected all {count} connections for user: {user_id}")
                return count
            return 0

    async def cleanup_stale_connections(self, max_age_seconds: int = 3600) -> int:
        """Cleanup stale connections"""
        async with self._lock:
            cleaned_count = 0
            current_time = time.time()

            # Cleanup user connections
            for user_id in list(self.user_connections.keys()):
                for server_id in list(self.user_connections[user_id].keys()):
                    connection = self.user_connections[user_id][server_id]

                    # Check if connection is stale
                    if current_time - connection.last_activity > max_age_seconds:
                        del self.user_connections[user_id][server_id]
                        cleaned_count += 1
                        logger.debug(f"Cleaned stale connection: {user_id}/{server_id}")

                # If user has no connections, cleanup user entry
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]

            logger.info(f"Cleaned {cleaned_count} stale connections")
            return cleaned_count

    async def get_connection_stats(self) -> dict[str, Any]:
        """Get connection statistics"""
        async with self._lock:
            total_app_connections = len(self.app_connections)
            total_user_connections = sum(len(conns) for conns in self.user_connections.values())
            total_users = len(self.user_connections)

            # Count connection statuses
            status_counts = {"connected": 0, "disconnected": 0, "connecting": 0, "error": 0, "unknown": 0}

            # Count application connection statuses
            for connection in self.app_connections.values():
                status = connection.connection_state.value
                if status in status_counts:
                    status_counts[status] += 1

            # Count user connection statuses
            for user_conns in self.user_connections.values():
                for connection in user_conns.values():
                    status = connection.connection_state.value
                    if status in status_counts:
                        status_counts[status] += 1

            return {
                "total_app_connections": total_app_connections,
                "total_user_connections": total_user_connections,
                "total_users": total_users,
                "status_counts": status_counts,
            }


_connection_service_instance: MCPConnectionService | None = None


async def get_connection_service() -> MCPConnectionService:
    """Get connection service instance"""
    global _connection_service_instance
    if _connection_service_instance is None:
        _connection_service_instance = MCPConnectionService()
        await _connection_service_instance.initialize_app_connections()
    return _connection_service_instance
