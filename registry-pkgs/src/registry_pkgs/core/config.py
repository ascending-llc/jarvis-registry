from pydantic import BaseModel, Field


class ChunkingConfig(BaseModel):
    max_chunk_size: int = Field(default=2048, description="Maximum size of text chunks for vectorization")
    chunk_overlap: int = Field(default=200, description="Overlap size between consecutive chunks")


class VectorConfig(BaseModel):
    vector_store_type: str = Field(default="weaviate", description="Vector database type")
    embedding_provider: str = Field(default="aws_bedrock", description="Embedding provider")
    weaviate_host: str = Field(default="127.0.0.1", description="Weaviate host address")
    weaviate_port: int = Field(default=8080, description="Weaviate port")
    weaviate_api_key: str | None = Field(default=None, description="Weaviate API key")
    weaviate_collection_prefix: str = Field(default="", description="Weaviate collection prefix")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    openai_model: str = Field(default="text-embedding-3-small", description="OpenAI embedding model")
    aws_region: str = Field(default="us-east-1", description="AWS region for Bedrock")
    bedrock_model: str = Field(default="amazon.titan-embed-text-v2:0", description="AWS Bedrock model")
    aws_access_key_id: str | None = Field(default=None, description="AWS access key ID")
    aws_secret_access_key: str | None = Field(default=None, description="AWS secret access key")
    aws_session_token: str | None = Field(default=None, description="AWS session token")
    azure_openai_api_key: str | None = Field(default=None, description="Azure OpenAI API key")
    azure_openai_endpoint: str = Field(default="", description="Azure OpenAI endpoint URL")
    azure_openai_api_version: str = Field(default="2024-06-01", description="Azure OpenAI API version")
    azure_openai_resource_name: str = Field(default="", description="Azure OpenAI resource name")
    azure_openai_embedding_deployment: str = Field(default="", description="Azure OpenAI embedding deployment name")
    azure_openai_llm_deployment: str = Field(default="", description="Azure OpenAI LLM deployment name")
    llm_model: str = Field(default="gpt-4", description="LLM model name")


class MongoConfig(BaseModel):
    mongo_uri: str = Field(
        default="mongodb://127.0.0.1:27017/jarvis",
        description="MongoDB connection URI (mongodb://host:port/dbname)",
    )
    mongodb_username: str = Field(default="", description="MongoDB username")
    mongodb_password: str = Field(default="", description="MongoDB password")


class RedisConfig(BaseModel):
    redis_uri: str = Field(default="redis://registry-redis:6379/1", description="Redis connection URI")
    redis_key_prefix: str = Field(default="jarvis-registry", description="Redis key prefix")


class TelemetryConfig(BaseModel):
    otel_metrics_config_path: str = Field(default="", description="Metrics config file path")
    otel_exporter_otlp_endpoint: str = Field(
        default="http://otel-collector:4318", description="OTLP collector endpoint"
    )
    otel_prometheus_enabled: bool = Field(default=False, description="Enable Prometheus metrics endpoint")
    otel_prometheus_port: int = Field(default=9464, description="Prometheus metrics port")


class ScopesConfig(BaseModel):
    scopes_config_path: str = Field(
        default="",
        description="Path to scopes.yml; package-bundled file is used when empty",
    )
