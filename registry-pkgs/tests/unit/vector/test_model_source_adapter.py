from types import SimpleNamespace

import pytest

from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig
from registry_pkgs.vector.config import model_source_adapter
from registry_pkgs.vector.config.config import AzureOpenAIEmbeddingConfig, BedrockEmbeddingConfig
from registry_pkgs.vector.enum.enums import EmbeddingProvider


def _source(provider_config):
    return SimpleNamespace(providerConfig=provider_config)


def test_bedrock_model_source_maps_to_bedrock_embedding_config():
    source = _source(
        AwsBedrockModelConfig(
            awsRegion="us-west-2",
            modelIdOrArn="amazon.titan-embed-text-v2:0",
            baseModelId="amazon.titan-embed-text-v2:0",
        )
    )

    config = model_source_adapter.embedding_config_from_model_source(source, encryption_key=b"unused")

    assert isinstance(config, BedrockEmbeddingConfig)
    assert config.provider == EmbeddingProvider.AWS_BEDROCK
    assert config.region == "us-west-2"
    assert config.model == "amazon.titan-embed-text-v2:0"
    # IRSA / default chain — no static credentials threaded from a ModelSource.
    assert config.access_key_id is None
    assert config.secret_access_key is None


def test_azure_model_source_decrypts_key_and_extracts_resource_name(monkeypatch):
    monkeypatch.setattr(model_source_adapter, "decrypt_value", lambda *_a, **_kw: "plain-key")
    source = _source(
        AzureOpenAIModelConfig(
            endpoint="https://acme.openai.azure.com",
            deploymentName="embed-deploy",
            baseModelId="text-embedding-3-small",
            apiVersion="2024-10-21",
            apiKeyEncrypted="iv:ciphertext",
        )
    )

    config = model_source_adapter.embedding_config_from_model_source(source, encryption_key=b"key")

    assert isinstance(config, AzureOpenAIEmbeddingConfig)
    assert config.provider == EmbeddingProvider.AZURE_OPENAI
    assert config.api_key == "plain-key"
    assert config.endpoint == "https://acme.openai.azure.com"
    assert config.api_version == "2024-10-21"
    assert config.resource_name == "acme"
    assert config.deployment_name == "embed-deploy"


def test_azure_model_source_without_api_key_raises():
    source = _source(
        AzureOpenAIModelConfig(
            endpoint="https://acme.openai.azure.com",
            deploymentName="embed-deploy",
            baseModelId="text-embedding-3-small",
            apiVersion="2024-10-21",
            apiKeyEncrypted=None,
        )
    )

    with pytest.raises(ValueError, match="requires apiKeyEncrypted"):
        model_source_adapter.embedding_config_from_model_source(source, encryption_key=b"key")
