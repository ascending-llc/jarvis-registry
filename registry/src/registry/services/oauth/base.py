"""
Abstract base classes for connection management.
Defines interfaces for connection lifecycle and state management.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ...schemas.enums import ConnectionState


@dataclass
class Connection(ABC):
    """Basic connection interface"""

    server_id: str
    connection_state: ConnectionState
    last_activity: float = field(default_factory=time.time)
    error_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    @abstractmethod
    def is_stale(self, max_idle_time: float | None = None) -> bool:
        """Return True if the connection is stale based on idle time and optional max_idle_time."""
        pass

    def update_activity(self) -> None:
        """Update last activity time"""
        self.last_activity = time.time()

    def increment_error(self) -> None:
        """Increase the error count."""
        self.error_count += 1

    def reset_errors(self) -> None:
        """Reset the error count."""
        self.error_count = 0


class ConnectionManager(ABC):
    """Connection Manager Abstract Interface"""

    @abstractmethod
    async def get_connection(self, user_id: str, server_id: str) -> Connection | None:
        pass

    @abstractmethod
    async def update_connection_state(
        self, user_id: str, server_id: str, state: ConnectionState, details: dict[str, Any] | None = None
    ) -> None:
        """update user connection state"""
        pass

    @abstractmethod
    async def create_user_connection(
        self,
        user_id: str,
        server_id: str,
        initial_state: ConnectionState = ConnectionState.CONNECTING,
        details: dict[str, Any] | None = None,
    ) -> Connection:
        """Create new user-level connection"""
        pass

    @abstractmethod
    async def disconnect_user_connection(self, user_id: str, server_id: str) -> bool:
        """Disconnect user connection"""
        pass

    @abstractmethod
    def get_user_connections(self, user_id: str) -> dict[str, Connection]:
        """Get all connections for the user"""
        pass


@dataclass
class ConnectionStateContext:
    """Connection status context - used for status parsing"""

    user_id: str
    server_name: str
    server_id: str
    server_config: dict[str, Any]
    connection: Connection | None = None
    is_oauth_server: bool = False
    idle_timeout: float | None = None  # idle_timeout value provided from server_config
