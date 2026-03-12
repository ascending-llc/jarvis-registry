from enum import StrEnum


class OAuthFlowStatus(StrEnum):
    """OAuth flow status"""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class ConnectionState(StrEnum):
    """Connection state enumeration"""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PENDING_OAUTH = "pending_oauth"
    ERROR = "error"
    UNKNOWN = "unknown"


class HealthStatus(StrEnum):
    """Health status constants for services."""

    HEALTHY = "healthy"
    HEALTHY_AUTH_EXPIRED = "healthy-auth-expired"
    UNHEALTHY_TIMEOUT = "unhealthy: timeout"
    UNHEALTHY_CONNECTION_ERROR = "unhealthy: connection error"
    UNHEALTHY_ENDPOINT_CHECK_FAILED = "unhealthy: endpoint check failed"
    UNHEALTHY_MISSING_PROXY_URL = "unhealthy: missing proxy URL"
    CHECKING = "checking"
    UNKNOWN = "unknown"

    @classmethod
    def get_healthy_statuses(cls) -> list[str]:
        """Get list of statuses that should be considered healthy for nginx inclusion."""
        return [cls.HEALTHY, cls.HEALTHY_AUTH_EXPIRED]

    @classmethod
    def is_healthy(cls, status: str) -> bool:
        """Check if a status should be considered healthy."""
        return status in cls.get_healthy_statuses()
