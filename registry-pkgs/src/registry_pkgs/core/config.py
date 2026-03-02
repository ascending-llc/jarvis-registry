from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """
    Unified configuration for MCP Gateway Registry.

    Environment variables are loaded from .env file.
    """

    # ========== Text Chunking Configuration ==========
    MAX_CHUNK_SIZE: int = Field(default=2048, description="Maximum size of text chunks for vectorization")
    CHUNK_OVERLAP: int = Field(default=200, description="Overlap size between consecutive chunks")

    # ========== Vector Store Configuration ==========
    VECTOR_STORE_TYPE: str = Field(default="weaviate", description="Vector database type (supported: weaviate)")
    EMBEDDING_PROVIDER: str = Field(
        default="aws_bedrock", description="Embedding provider (supported: openai, aws_bedrock)"
    )

    # ========== Weaviate Configuration ==========
    WEAVIATE_HOST: str = Field(default="127.0.0.1", description="Weaviate server host address")
    WEAVIATE_PORT: str = Field(default="8080", description="Weaviate server port")
    WEAVIATE_API_KEY: str = Field(default="", description="Weaviate API key for authentication (optional)")
    WEAVIATE_COLLECTION_PREFIX: str = Field(default="", description="Prefix for Weaviate collection names (optional)")

    # ========== OpenAI Configuration ==========
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key (required when EMBEDDING_PROVIDER=openai)")
    OPENAI_MODEL: str = Field(default="text-embedding-3-small", description="OpenAI embedding model name")

    # ========== AWS Bedrock Configuration ==========
    AWS_REGION: str = Field(default="us-east-1", description="AWS region for Bedrock service")
    BEDROCK_MODEL: str = Field(default="amazon.titan-embed-text-v2:0", description="AWS Bedrock embedding model name")
    AWS_ACCESS_KEY_ID: str = Field(
        default="", description="AWS access key ID (optional, uses default credentials chain if not set)"
    )
    AWS_SECRET_ACCESS_KEY: str = Field(
        default="", description="AWS secret access key (optional, uses default credentials chain if not set)"
    )

    # ========== MongoDB Configuration ==========
    MONGO_URI: str = Field(
        default="mongodb://127.0.0.1:27017/jarvis",
        description="MongoDB connection URI (format: mongodb://host:port/dbname)",
    )
    MONGODB_USERNAME: str = Field(default="", description="MongoDB username for authentication (optional)")
    MONGODB_PASSWORD: str = Field(default="", description="MongoDB password for authentication (optional)")

    # ========== OpenTelemetry Configuration ==========
    OTEL_METRICS_CONFIG_PATH: str = Field(default="", description="Path to the OpenTelemetry metrics config file")

    # ========== Scopes Configuration ==========
    SCOPES_CONFIG_PATH: str = Field(
        default="",
        description="Path to scopes.yml configuration file (optional, uses package-bundled file if not set)",
    )

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="allow",  # Allow extra fields
    )


settings = Settings()
