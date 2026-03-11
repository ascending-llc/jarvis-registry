"""
Constants and enums for the MCP Gateway Registry.
"""

import os
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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


class TransportType(StrEnum):
    """Supported transport types for MCP servers."""

    STREAMABLE_HTTP = "streamable-http"
    SSE = "sse"


class RegistryConstants(BaseModel):
    """Registry configuration constants."""

    model_config = ConfigDict(frozen=True)

    # Health check settings
    DEFAULT_HEALTH_CHECK_TIMEOUT: int = 30
    HEALTH_CHECK_INTERVAL: int = 30

    # SSL certificate paths
    SSL_CERT_PATH: str = "/etc/ssl/certs/fullchain.pem"
    SSL_KEY_PATH: str = "/etc/ssl/private/privkey.pem"

    # Nginx settings
    NGINX_CONFIG_PATH: str = "/etc/nginx/conf.d/nginx_rev_proxy.conf"
    NGINX_TEMPLATE_HTTP_ONLY: str = "/app/docker/nginx_rev_proxy_http_only.conf"
    NGINX_TEMPLATE_HTTP_AND_HTTPS: str = "/app/docker/nginx_rev_proxy_http_and_https.conf"
    NGINX_TEMPLATE_HTTP_ONLY_LOCAL: str = "docker/nginx_rev_proxy_http_only.conf"
    NGINX_TEMPLATE_HTTP_AND_HTTPS_LOCAL: str = "docker/nginx_rev_proxy_http_and_https.conf"

    # Server settings
    DEFAULT_TRANSPORT: str = TransportType.STREAMABLE_HTTP
    SUPPORTED_TRANSPORTS: list[str] = [TransportType.STREAMABLE_HTTP, TransportType.SSE]

    # Anthropic Registry API constants
    ANTHROPIC_API_VERSION: str = "v0.1"
    ANTHROPIC_SERVER_NAMESPACE: str = "io.mcpgateway"
    ANTHROPIC_API_DEFAULT_LIMIT: int = 100
    ANTHROPIC_API_MAX_LIMIT: int = 1000

    # External Registry Tags
    # Comma-separated list of tags that identify external registry servers
    # Example: "anthropic-registry,workday-asor,custom-registry"
    EXTERNAL_REGISTRY_TAGS: str = os.getenv("EXTERNAL_REGISTRY_TAGS", "anthropic-registry,workday-asor")
    # Weaviate Configuration
    WEAVIATE_HOST: str = os.getenv("WEAVIATE_HOST", "weaviate")
    WEAVIATE_PORT: int = int(os.getenv("WEAVIATE_PORT", "8080"))
    WEAVIATE_API_KEY: str | None = os.getenv("WEAVIATE_API_KEY", "test-secret-key")
    WEAVIATE_EMBEDDINGS_PROVIDER: str = os.getenv("WEAVIATE_EMBEDDINGS_PROVIDER", "bedrock")

    WEAVIATE_SESSION_POOL_CONNECTIONS: int = 20  # Maximum connections
    WEAVIATE_SESSION_POOL_MAXSIZE: int = 100  # Connection pool size
    WEAVIATE_INIT_TIME: int = 30
    WEAVIATE_QUERY_TIME: int = 120
    WEAVIATE_INSERT_TIME: int = 300

    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_SESSION_TOKEN: str = os.getenv("AWS_SESSION_TOKEN")
    AWS_REGION: str = os.getenv("AWS_REGION")
    AGENTCORE_ASSUME_ROLE_ARN: str | None = os.getenv("AGENTCORE_ASSUME_ROLE_ARN")

    REDIS_URI: str = os.getenv("REDIS_URI", "redis://registry-redis:6379/1")
    REDIS_KEY_PREFIX: str = os.getenv("REDIS_KEY_PREFIX", "jarvis-registry")

    REGISTRY_CLIENT_URL: str = os.getenv("REGISTRY_CLIENT_URL", "http://localhost:5173")


# Global instance
REGISTRY_CONSTANTS = RegistryConstants()
