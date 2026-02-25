import argparse
import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Constants:
    """Application constants that don't change."""

    DESCRIPTION: str = "MCP Gateway Registry Interaction Server (mcpgw)"
    DEFAULT_MCP_TRANSPORT: str = "streamable-http"
    DEFAULT_MCP_SERVER_LISTEN_PORT: str = "8003"
    REQUEST_TIMEOUT: float = 30.0


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings can be overridden via environment variables with the same name.
    For nested configs, use double underscore (e.g., REGISTRY__URL).
    """

    # Registry configuration
    REGISTRY_URL: str = Field(default="http://localhost:7860", description="Base URL of the MCP Gateway Registry")

    # Server configuration
    MCP_TRANSPORT: str = Field(default=Constants.DEFAULT_MCP_TRANSPORT, description="Transport type for the MCP server")
    MCP_SERVER_HOST: str = Field(
        default="0.0.0.0",
        description="Host interface for the MCP server to bind to (0.0.0.0 for all interfaces, 127.0.0.1 for localhost only)",
    )
    MCP_SERVER_LISTEN_PORT: str = Field(
        default=Constants.DEFAULT_MCP_SERVER_LISTEN_PORT, description="Port for the MCP server to listen on"
    )

    # Auth server configuration
    AUTH_SERVER_URL: str = Field(default="http://localhost:8888", description="URL of the authentication server")

    INTERNAL_AUTH_HEADER: str = Field(
        default="X-Jarvis-Auth", description="Header name for internal JWT authentication (RFC 8707 compliant)"
    )
    # JWT authentication configuration
    SECRET_KEY: str = Field(default="", description="Secret key for JWT token validation (HS256)")
    JWT_ISSUER: str = Field(default="jarvis-auth-server", description="Expected JWT token issuer")
    JWT_AUDIENCE: str = Field(default="jarvis-registry", description="Expected JWT token audience")
    JWT_SELF_SIGNED_KID: str = Field(default="self-signed-key-v1", description="Key ID for self-signed JWT tokens")

    # Logging configuration
    log_level: str = Field(default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    log_format: str = Field(
        default="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
        description="Logging format string",
    )

    API_VERSION: str = "v1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Enable environment variable validation
        validate_default=True,
    )

    @field_validator("REGISTRY_URL")
    @classmethod
    def validate_registry_url(cls, v: str) -> str:
        """Validate and normalize registry base URL."""
        if not v:
            raise ValueError("REGISTRY_URL must be set")
        return v.rstrip("/")

    def configure_logging(self) -> None:
        """Configure application-wide logging with consistent format and level.

        This should be called once at application startup to initialize logging
        for all modules. Individual modules can then use logging.getLogger(__name__)
        without needing to call basicConfig again.
        """
        # Convert string log level to numeric level
        numeric_level = getattr(logging, self.log_level.upper(), logging.INFO)

        logging.basicConfig(
            level=numeric_level,
            format=self.log_format,
            force=True,  # Override any existing configuration
        )

    def log_config(self):
        """Log current configuration (hiding sensitive values)."""
        logger.info("Configuration loaded:")
        logger.info(f"  Registry URL: {self.REGISTRY_URL}")
        logger.info(f"  MCP Transport: {self.MCP_TRANSPORT}")
        logger.info(f"  Listen Port: {self.MCP_SERVER_LISTEN_PORT}")
        logger.info(f"  Auth Server URL: {self.AUTH_SERVER_URL}")
        logger.info(f"  Log Level: {self.log_level}")


# Create global settings instance
settings = Settings()

# Configure logging on module import
settings.configure_logging()
settings.log_config()


def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.

    Command line arguments override environment variables.

    Returns:
        argparse.Namespace: Parsed command line arguments
    """
    parser = argparse.ArgumentParser(description=Constants.DESCRIPTION)
    parser.add_argument(
        "--port",
        type=str,
        help=f"Port for the MCP server to listen on (default: from env or {Constants.DEFAULT_MCP_SERVER_LISTEN_PORT})",
    )

    parser.add_argument(
        "--transport",
        type=str,
        choices=["streamable-http"],
        help=f"Transport type for the MCP server (default: from env or {Constants.DEFAULT_MCP_TRANSPORT})",
    )
    return parser.parse_args()
