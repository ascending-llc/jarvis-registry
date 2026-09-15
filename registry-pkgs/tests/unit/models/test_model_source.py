import pytest
from pydantic import TypeAdapter, ValidationError

from registry_pkgs.models.enums import ModelSourceMode, ModelSourceProviderType
from registry_pkgs.models.model_source import (
    AwsBedrockModelConfig,
    AzureOpenAIModelConfig,
    ModelSource,
    ModelSourceProviderConfig,
)

_config_adapter = TypeAdapter(ModelSourceProviderConfig)


def test_bedrock_config_parses_from_discriminator() -> None:
    config = _config_adapter.validate_python(
        {
            "providerType": "aws_bedrock",
            "awsRegion": "us-east-1",
            "modelIdOrArn": "anthropic.claude-3-5-sonnet",
            "baseModelId": "anthropic.claude-3-5-sonnet",
        }
    )
    assert isinstance(config, AwsBedrockModelConfig)
    assert config.providerType == ModelSourceProviderType.AWS_BEDROCK


def test_azure_config_parses_from_discriminator() -> None:
    config = _config_adapter.validate_python(
        {
            "providerType": "azure_openai",
            "endpoint": "https://acme.openai.azure.com",
            "deploymentName": "gpt4o-prod",
            "baseModelId": "gpt-4o",
            "apiVersion": "2024-10-21",
        }
    )
    assert isinstance(config, AzureOpenAIModelConfig)
    assert config.apiKeyEncrypted is None


def test_config_round_trips_through_serialized_form() -> None:
    """A stored (JSON-dumped) config re-parses back into the correct typed variant."""
    original = _config_adapter.validate_python(
        {
            "providerType": "azure_openai",
            "endpoint": "https://acme.openai.azure.com",
            "deploymentName": "d",
            "baseModelId": "gpt-4o",
            "apiVersion": "2024-10-21",
            "apiKeyEncrypted": "iv:ciphertext",
        }
    )
    reparsed = _config_adapter.validate_python(original.model_dump(mode="json"))
    assert isinstance(reparsed, AzureOpenAIModelConfig)
    assert reparsed.apiKeyEncrypted == "iv:ciphertext"


def test_unknown_provider_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _config_adapter.validate_python({"providerType": "vertex_ai", "region": "x"})


def test_bedrock_requires_base_model_id() -> None:
    with pytest.raises(ValidationError):
        _config_adapter.validate_python({"providerType": "aws_bedrock", "awsRegion": "us-east-1", "modelIdOrArn": "m"})


def test_model_source_defaults() -> None:
    source = ModelSource.model_construct(
        displayName="Claude Sonnet",
        mode=ModelSourceMode.CHAT,
        providerConfig=AwsBedrockModelConfig(
            awsRegion="us-east-1",
            modelIdOrArn="anthropic.claude-3-5-sonnet",
            baseModelId="anthropic.claude-3-5-sonnet",
        ),
    )
    assert source.tags == []
    assert source.deletedAt is None
    assert source.description is None
