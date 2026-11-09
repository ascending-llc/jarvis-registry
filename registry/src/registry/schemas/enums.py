from enum import StrEnum


class ConnectionState(StrEnum):
    """Connection state enumeration"""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PENDING_OAUTH = "pending_oauth"
    ERROR = "error"
    UNKNOWN = "unknown"


class TokenPurpose(StrEnum):
    """Purpose of a user-vended managed-agent token."""

    INTERACTIVE = "interactive"
    AGENT = "agent"
