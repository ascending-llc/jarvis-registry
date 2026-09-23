from __future__ import annotations

from registry_pkgs.core.crypto_utils import decrypt_value
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource
from registry_pkgs.vector.config.config import (
    AzureOpenAIEmbeddingConfig,
    BedrockEmbeddingConfig,
    EmbeddingModelConfig,
    extract_azure_resource_name,
)
from registry_pkgs.vector.enum.enums import EmbeddingProvider


def embedding_config_from_model_source(model_source: ModelSource, *, encryption_key: bytes) -> EmbeddingModelConfig:
    """Build the embedding backend config for one ModelSource selected as the embedding default."""
    config = model_source.providerConfig
    if isinstance(config, AwsBedrockModelConfig):
        return BedrockEmbeddingConfig(
            provider=EmbeddingProvider.AWS_BEDROCK,
            region=config.awsRegion,
            model=config.modelIdOrArn,
            # access_key_id/secret_access_key/session_token intentionally unset — IRSA/default chain.
        )
    if isinstance(config, AzureOpenAIModelConfig):
        if not config.apiKeyEncrypted:
            raise ValueError(
                "Azure OpenAI ModelSource used for embedding requires apiKeyEncrypted — Workload "
                "Identity is not supported for embedding in this version."
            )
        return AzureOpenAIEmbeddingConfig(
            provider=EmbeddingProvider.AZURE_OPENAI,
            api_key=decrypt_value(config.apiKeyEncrypted, encryption_key=encryption_key),
            endpoint=config.endpoint,
            api_version=config.apiVersion,
            resource_name=extract_azure_resource_name(config.endpoint),
            deployment_name=config.deploymentName,
        )
    raise ValueError(f"Unsupported ModelSource provider config for embedding: {type(config).__name__}")
