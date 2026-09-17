from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agno.models.aws import AwsBedrock
from agno.models.azure import AzureOpenAI
from beanie import PydanticObjectId

from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource
from registry_pkgs.workflows import model_resolution


def _source(provider_config, *, mode: ModelSourceMode = ModelSourceMode.CHAT) -> ModelSource:
    return ModelSource.model_construct(
        id=PydanticObjectId(),
        displayName="model",
        mode=mode,
        providerConfig=provider_config,
        deletedAt=None,
    )


def test_build_agno_model_for_bedrock() -> None:
    source = _source(
        AwsBedrockModelConfig(
            awsRegion="us-east-1",
            modelIdOrArn="anthropic.claude-v1",
            baseModelId="anthropic.claude-v1",
        )
    )

    model = model_resolution.build_agno_model(
        source,
        encryption_key=b"unused",
        azure_ad_token_provider=None,
    )

    assert isinstance(model, AwsBedrock)
    assert model.id == "anthropic.claude-v1"
    assert model.aws_region == "us-east-1"


def test_build_legacy_bedrock_model() -> None:
    model = model_resolution.build_legacy_bedrock_model("amazon.nova-lite-v1:0", "us-west-2")

    assert isinstance(model, AwsBedrock)
    assert model.id == "amazon.nova-lite-v1:0"
    assert model.aws_region == "us-west-2"


def test_build_agno_model_for_bedrock_application_inference_profile() -> None:
    # agno's native AwsBedrock (Converse) accepts an AIP ARN as the model id directly.
    profile_arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/profile-id"
    source = _source(
        AwsBedrockModelConfig(
            awsRegion="us-east-1",
            modelIdOrArn=profile_arn,
            baseModelId="amazon.nova-lite-v1:0",
        )
    )

    model = model_resolution.build_agno_model(
        source,
        encryption_key=b"unused",
        azure_ad_token_provider=None,
    )

    assert model.id == profile_arn
    assert model.aws_region == "us-east-1"


def test_build_agno_model_for_azure_decrypts_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_resolution, "decrypt_value", lambda *_a, **_kw: "plain-key")
    source = _source(
        AzureOpenAIModelConfig(
            endpoint="https://example.openai.azure.com",
            deploymentName="chat",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
            apiKeyEncrypted="encrypted",
        )
    )

    model = model_resolution.build_agno_model(
        source,
        encryption_key=b"key",
        azure_ad_token_provider=None,
    )

    assert isinstance(model, AzureOpenAI)
    assert model.id == "gpt-4o"
    assert model.azure_deployment == "chat"
    assert model.azure_endpoint == "https://example.openai.azure.com"
    assert model.api_version == "2024-10-21"
    assert model.api_key == "plain-key"


def test_build_agno_model_for_azure_workload_identity_uses_shared_provider() -> None:
    def token_provider() -> str:
        return "token"

    source = _source(
        AzureOpenAIModelConfig(
            endpoint="https://example.openai.azure.com",
            deploymentName="chat",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
        )
    )

    model = model_resolution.build_agno_model(
        source,
        encryption_key=b"unused",
        azure_ad_token_provider=token_provider,
    )

    assert isinstance(model, AzureOpenAI)
    assert model.id == "gpt-4o"
    assert model.azure_deployment == "chat"
    assert model.azure_endpoint == "https://example.openai.azure.com"
    assert model.api_version == "2024-10-21"
    assert model.azure_ad_token_provider is token_provider
    assert model.api_key is None


def test_build_agno_model_for_azure_workload_identity_requires_shared_provider() -> None:
    source = _source(
        AzureOpenAIModelConfig(
            endpoint="https://example.openai.azure.com",
            deploymentName="chat",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
        )
    )

    with pytest.raises(RuntimeError, match="app-scoped token provider"):
        model_resolution.build_agno_model(
            source,
            encryption_key=b"unused",
            azure_ad_token_provider=None,
        )


def test_build_agno_model_rejects_unknown_provider_config() -> None:
    source = ModelSource.model_construct(providerConfig=object())

    with pytest.raises(TypeError, match="Unsupported model source provider config: object"):
        model_resolution.build_agno_model(
            source,
            encryption_key=b"unused",
            azure_ad_token_provider=None,
        )


def test_azure_model_credential_owns_and_closes_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = SimpleNamespace(close=MagicMock())
    token_provider = object()
    get_provider = MagicMock(return_value=token_provider)
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr("azure.identity.get_bearer_token_provider", get_provider)

    owner = model_resolution.AzureModelCredential()
    owner.close()

    assert owner.token_provider is token_provider
    get_provider.assert_called_once_with(credential, "https://cognitiveservices.azure.com/.default")
    credential.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_resolve_model_uses_fallback_for_invalid_unavailable_or_non_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = object()
    assert (
        await model_resolution.resolve_model(
            "invalid",
            fallback_model=fallback,
            encryption_key=b"key",
            azure_ad_token_provider=None,
        )
        is fallback
    )

    monkeypatch.setattr(model_resolution.ModelSource, "get", AsyncMock(return_value=None))
    assert (
        await model_resolution.resolve_model(
            str(PydanticObjectId()),
            fallback_model=fallback,
            encryption_key=b"key",
            azure_ad_token_provider=None,
        )
        is fallback
    )

    embedding = _source(
        AwsBedrockModelConfig(awsRegion="us-east-1", modelIdOrArn="embed", baseModelId="embed"),
        mode=ModelSourceMode.EMBEDDING,
    )
    monkeypatch.setattr(model_resolution.ModelSource, "get", AsyncMock(return_value=embedding))
    assert (
        await model_resolution.resolve_model(
            str(embedding.id),
            fallback_model=fallback,
            encryption_key=b"key",
            azure_ad_token_provider=None,
        )
        is fallback
    )


@pytest.mark.asyncio
async def test_resolve_default_reads_selection_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    source_id = PydanticObjectId()
    get_selection = AsyncMock(return_value=SimpleNamespace(defaultWorkflowModelSourceId=source_id))
    resolve = AsyncMock(return_value="resolved")
    monkeypatch.setattr(model_resolution, "get_model_gateway_selection", get_selection)
    monkeypatch.setattr(model_resolution, "resolve_model", resolve)

    result = await model_resolution.resolve_default_workflow_model(
        fallback_model="fallback",
        encryption_key=b"key",
        azure_ad_token_provider=None,
    )

    assert result == "resolved"
    get_selection.assert_awaited_once_with(create_if_missing=False)
    resolve.assert_awaited_once_with(
        source_id,
        fallback_model="fallback",
        encryption_key=b"key",
        azure_ad_token_provider=None,
    )
