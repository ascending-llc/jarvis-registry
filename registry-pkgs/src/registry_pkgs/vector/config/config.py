from pydantic import BaseModel, Field

from ...core.config import VectorConfig
from ..enum.enums import EmbeddingProvider, VectorStoreType


class VectorStoreConfig(BaseModel):
    """Base class for vector store configuration."""

    type: VectorStoreType

    @classmethod
    def from_vector_config(cls, config: VectorConfig) -> "VectorStoreConfig":
        """Create config instance from shared vector config."""
        raise NotImplementedError(f"{cls.__name__} must implement from_vector_config")


class EmbeddingModelConfig(BaseModel):
    """Base class for embedding model configuration."""

    provider: EmbeddingProvider

    @classmethod
    def from_vector_config(cls, config: VectorConfig) -> "EmbeddingModelConfig":
        """Create config instance from shared vector config."""
        raise NotImplementedError(f"{cls.__name__} must implement from_vector_config")


# Registry for configuration classes
_VECTOR_STORE_REGISTRY: dict[str, type[VectorStoreConfig]] = {}
_EMBEDDING_MODEL_REGISTRY: dict[str, type[EmbeddingModelConfig]] = {}


def register_vector_store_config(name: VectorStoreType | str):
    """Decorator to register vector store config class."""

    def decorator(config_class: type[VectorStoreConfig]):
        # Extract string value from enum if needed
        key = name.value if isinstance(name, VectorStoreType) else name
        _VECTOR_STORE_REGISTRY[key] = config_class
        return config_class

    return decorator


def register_embedding_model_config(name: EmbeddingProvider | str):
    """Decorator to register embedding model config class."""

    def decorator(config_class: type[EmbeddingModelConfig]):
        # Extract string value from enum if needed
        key = name.value if isinstance(name, EmbeddingProvider) else name
        _EMBEDDING_MODEL_REGISTRY[key] = config_class
        return config_class

    return decorator


def get_vector_store_config_class(name: str) -> type[VectorStoreConfig]:
    """Get vector store config class by name."""
    if name not in _VECTOR_STORE_REGISTRY:
        available = list(_VECTOR_STORE_REGISTRY.keys())
        raise ValueError(f"Unknown vector store type: {name}. Available: {available}")
    return _VECTOR_STORE_REGISTRY[name]


def get_embedding_model_config_class(name: str) -> type[EmbeddingModelConfig]:
    """Get embedding model config class by name."""
    if name not in _EMBEDDING_MODEL_REGISTRY:
        available = list(_EMBEDDING_MODEL_REGISTRY.keys())
        raise ValueError(f"Unknown embedding provider: {name}. Available: {available}")
    return _EMBEDDING_MODEL_REGISTRY[name]


def get_registered_vector_stores() -> list:
    """Get list of registered vector store types."""
    return list(_VECTOR_STORE_REGISTRY.keys())


def get_registered_embedding_models() -> list:
    """Get list of registered embedding providers."""
    return list(_EMBEDDING_MODEL_REGISTRY.keys())


@register_vector_store_config(VectorStoreType.WEAVIATE)
class WeaviateConfig(VectorStoreConfig):
    """Weaviate vector store configuration."""

    host: str = Field(description="Weaviate host")
    port: int = Field(description="Weaviate port")
    api_key: str | None = Field(default=None, description="API key")
    collection_prefix: str | None = Field(default=None, description="Collection prefix")

    @classmethod
    def from_vector_config(cls, config: VectorConfig) -> "WeaviateConfig":
        """Create Weaviate config from explicit vector config."""
        host = config.weaviate_host
        port = config.weaviate_port
        collection_prefix = config.weaviate_collection_prefix

        # Required validation
        if not host:
            raise ValueError("weaviate_host must be set in the supplied vector config")
        if not port:
            raise ValueError("weaviate_port must be set in the supplied vector config")

        # Type validation
        port_int = int(port)
        if port_int <= 0 or port_int > 65535:
            raise ValueError(f"WEAVIATE_PORT must be between 1-65535, got: {port_int}")

        # Host validation
        if not host or host.strip() == "":
            raise ValueError("WEAVIATE_HOST cannot be empty")

        return cls(
            type=VectorStoreType.WEAVIATE,
            host=host.strip(),
            port=port_int,
            api_key=config.weaviate_api_key,
            collection_prefix=collection_prefix,
        )


@register_embedding_model_config(EmbeddingProvider.OPENAI)
class OpenAIEmbeddingConfig(EmbeddingModelConfig):
    """OpenAI embedding model configuration."""

    api_key: str = Field(description="OpenAI API key")
    model: str = Field(description="Model name")

    @classmethod
    def from_vector_config(cls, config: VectorConfig) -> "OpenAIEmbeddingConfig":
        """Create OpenAI config from explicit vector config."""
        api_key = config.openai_api_key
        model = config.openai_model

        # Required validation
        if not api_key or api_key.strip() == "":
            raise ValueError("openai_api_key must be set in the supplied vector config")

        # API key format validation
        if not api_key.startswith("sk-"):
            raise ValueError("OPENAI_API_KEY must start with 'sk-'")

        # Model validation (basic)
        if not model or model.strip() == "":
            model = "text-embedding-3-small"

        return cls(provider=EmbeddingProvider.OPENAI, api_key=api_key.strip(), model=model.strip())


@register_embedding_model_config(EmbeddingProvider.AWS_BEDROCK)
class BedrockEmbeddingConfig(EmbeddingModelConfig):
    """AWS Bedrock embedding model configuration."""

    region: str = Field(description="AWS region")
    model: str = Field(description="Bedrock model ID")
    access_key_id: str | None = Field(default=None, description="AWS access key ID")
    secret_access_key: str | None = Field(default=None, description="AWS secret access key")

    @classmethod
    def from_vector_config(cls, config: VectorConfig) -> "BedrockEmbeddingConfig":
        """Create AWS Bedrock config from explicit vector config."""
        region = config.aws_region
        model = config.bedrock_model

        # Required validation
        if not region or region.strip() == "":
            raise ValueError("aws_region must be set in the supplied vector config")

        # Strip whitespace before validation
        region = region.strip()

        # Region format validation
        valid_regions = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1", "ap-northeast-1"]
        if region not in valid_regions:
            raise ValueError(
                f"AWS_REGION '{region}' may not support Bedrock. Common regions: {', '.join(valid_regions)}"
            )

        # Model validation
        if not model or model.strip() == "":
            model = "amazon.titan-embed-text-v2:0"

        return cls(
            provider=EmbeddingProvider.AWS_BEDROCK,
            region=region,
            model=model.strip(),
            access_key_id=config.aws_access_key_id,
            secret_access_key=config.aws_secret_access_key,
        )


@register_embedding_model_config(EmbeddingProvider.AZURE_OPENAI)
class AzureOpenAIEmbeddingConfig(EmbeddingModelConfig):
    """Azure OpenAI embedding model configuration."""

    api_key: str = Field(description="Azure OpenAI API key")
    endpoint: str = Field(description="Azure OpenAI endpoint URL")
    api_version: str = Field(description="Azure OpenAI API version")
    resource_name: str = Field(description="Azure OpenAI resource name")
    deployment_name: str = Field(description="Azure OpenAI deployment name")

    @classmethod
    def from_vector_config(cls, config: VectorConfig) -> "AzureOpenAIEmbeddingConfig":
        """Create Azure OpenAI config from explicit vector config."""
        api_key = config.azure_openai_api_key
        endpoint = config.azure_openai_endpoint
        api_version = config.azure_openai_api_version
        resource_name = config.azure_openai_resource_name
        deployment_name = config.azure_openai_embedding_deployment

        # Required validation
        if not api_key or api_key.strip() == "":
            raise ValueError(
                "azure_openai_api_key must be set in the supplied vector config. "
                "Please set the AZURE_OPENAI_API_KEY environment variable."
            )

        if not endpoint or endpoint.strip() == "":
            raise ValueError(
                "azure_openai_endpoint must be set in the supplied vector config. "
                "Please set the AZURE_OPENAI_ENDPOINT environment variable."
            )

        if not deployment_name or deployment_name.strip() == "":
            raise ValueError(
                "azure_openai_embedding_deployment must be set in the supplied vector config. "
                "Please set the AZURE_OPENAI_EMBEDDING_DEPLOYMENT environment variable."
            )

        # Endpoint format validation
        endpoint = endpoint.strip()
        if not endpoint.startswith("https://"):
            raise ValueError("azure_openai_endpoint must start with 'https://'")

        if not endpoint.endswith(".openai.azure.com") and not endpoint.endswith(".openai.azure.com/"):
            raise ValueError("azure_openai_endpoint must be a valid Azure OpenAI endpoint (*.openai.azure.com)")

        # Extract resource_name from endpoint if not provided
        if not resource_name or resource_name.strip() == "":
            # Extract from endpoint: https://resource-name.openai.azure.com/ -> resource-name
            import re

            match = re.match(r"https://([^.]+)\.openai\.azure\.com", endpoint)
            if match:
                resource_name = match.group(1)
            else:
                raise ValueError(
                    "Failed to extract resource_name from endpoint. "
                    "Please set AZURE_OPENAI_RESOURCE_NAME environment variable."
                )

        # API version validation
        if not api_version or api_version.strip() == "":
            api_version = "2024-06-01"

        return cls(
            provider=EmbeddingProvider.AZURE_OPENAI,
            api_key=api_key.strip(),
            endpoint=endpoint.rstrip("/"),
            api_version=api_version.strip(),
            resource_name=resource_name.strip(),
            deployment_name=deployment_name.strip(),
        )


class BackendConfig(BaseModel):
    """Unified backend configuration."""

    vector_store_config: VectorStoreConfig
    embedding_model_config: EmbeddingModelConfig

    @classmethod
    def from_vector_config(cls, config: VectorConfig) -> "BackendConfig":
        """
        Create unified backend config from an explicit vector config model.

        Required fields:
        - vector_store_type: weaviate
        - embedding_provider: openai | aws_bedrock

        Raises:
            ValueError: If required fields are missing or invalid
        """
        vector_store_type = config.vector_store_type
        embedding_provider = config.embeddings_provider

        # Required validation
        if not vector_store_type or vector_store_type.strip() == "":
            raise ValueError(
                "vector_store_type must be set in the supplied vector config. "
                f"Valid values: {', '.join(get_registered_vector_stores())}"
            )
        if not embedding_provider or embedding_provider.strip() == "":
            raise ValueError(
                "embedding_provider must be set in the supplied vector config. "
                f"Valid values: {', '.join(get_registered_embedding_models())}"
            )

        # Normalize
        vector_store_type = vector_store_type.strip().lower()
        embedding_provider = embedding_provider.strip().lower()

        # Validate against registered types
        if vector_store_type not in get_registered_vector_stores():
            raise ValueError(
                f"Unsupported vector_store_type: '{vector_store_type}'. "
                f"Supported: {', '.join(get_registered_vector_stores())}"
            )

        if embedding_provider not in get_registered_embedding_models():
            raise ValueError(
                f"Unsupported embedding_provider: '{embedding_provider}'. "
                f"Supported: {', '.join(get_registered_embedding_models())}"
            )

        # Create configs
        vector_store_class = get_vector_store_config_class(vector_store_type)
        embedding_class = get_embedding_model_config_class(embedding_provider)

        return cls(
            vector_store_config=vector_store_class.from_vector_config(config),
            embedding_model_config=embedding_class.from_vector_config(config),
        )

    @property
    def vector_store_type(self) -> str:
        """Get vector store type."""
        return self.vector_store_config.type

    @property
    def embedding_provider(self) -> str:
        """Get embedding provider."""
        return self.embedding_model_config.provider

    def get_vector_store_config_dict(self) -> dict:
        """Get vector store config as dictionary."""
        return self.vector_store_config.model_dump()

    def get_embedding_model_config_dict(self) -> dict:
        """Get embedding model config as dictionary."""
        return self.embedding_model_config.model_dump()
