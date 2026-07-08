"""Environment settings for Travel Assistant Agent."""

import logging
import os

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _get_bedrock_model_id() -> str:
    return os.getenv("BEDROCK_MODEL_ID") or os.getenv("AWS_BEDROCK_SONNET_AIP_ARN") or DEFAULT_BEDROCK_MODEL_ID


class EnvSettings:
    """Environment settings configuration."""

    def __init__(self) -> None:
        """Initialize environment settings."""
        self.db_path: str = os.getenv("DB_PATH", "/app/data/flights.db")
        self.aws_region: str = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self.bedrock_model_id: str = _get_bedrock_model_id()
        self.agent_name: str = os.getenv("AGENT_NAME", "travel-assistant")
        self.agent_version: str = os.getenv("AGENT_VERSION", "1.0.0")

        # MCP Gateway Registry URL (TODO: replace later)
        self.mcp_registry_url: str = os.getenv("MCP_REGISTRY_URL", "http://localhost:7860")

        # Agent's public URL (AgentCore Runtime injects automatically)
        self.agent_url: str = os.getenv("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")

        # Server configuration (fixed for A2A protocol)
        self.host: str = "0.0.0.0"
        self.port: int = 9000

        logger.info(
            f"EnvSettings initialized: agent_name={self.agent_name}, "
            f"version={self.agent_version}, bedrock_model_configured={bool(self.bedrock_model_id)}"
        )
        logger.debug(f"Database path: {self.db_path}")
        logger.debug(f"Agent URL: {self.agent_url}")
