from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agno.models.litellm import LiteLLM
from beanie import PydanticObjectId

from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_source import (
    AnthropicModelConfig,
    AwsBedrockModelConfig,
    AzureOpenAIModelConfig,
    ModelSource,
    OpenAIModelConfig,
)
from registry_pkgs.workflows import model_resolution
from registry_pkgs.workflows.types import WorkflowConfigError


def _source(provider_config, *, mode: ModelSourceMode = ModelSourceMode.CHAT) -> ModelSource:
    return ModelSource.model_construct(
        id=PydanticObjectId(),
        displayName="model",
        mode=mode,
        providerConfig=provider_config,
        deletedAt=None,
    )


def test_bedrock_litellm_model_id_plain_id() -> None:
    assert model_resolution.bedrock_litellm_model_id("amazon.nova-lite-v1:0") == "bedrock/amazon.nova-lite-v1:0"


@pytest.mark.parametrize(
    "model_id",
    [
        "global.openai.gpt-6.1-sol",  # system profile id, not an ARN: LiteLLM routes it itself
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0",
        "arn:aws:bedrock:us-east-1:123456789012:imported-model/abc123",  # imported models need InvokeModel
    ],
)
def test_bedrock_litellm_model_id_leaves_non_profile_ids_to_litellm(model_id: str) -> None:
    assert model_resolution.bedrock_litellm_model_id(model_id) == f"bedrock/{model_id}"


@pytest.mark.parametrize(
    "arn",
    [
        "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/profile-id",
        # System cross-region profile of a model LiteLLM 1.80 doesn't list: unpinned it routed to InvokeModel.
        "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.openai.gpt-6.1-sol",
        "arn:aws-us-gov:bedrock:us-gov-west-1:123456789012:inference-profile/us-gov.anthropic.claude-v1",
    ],
)
def test_bedrock_litellm_model_id_pins_inference_profile_arns_to_converse(arn: str) -> None:
    assert model_resolution.bedrock_litellm_model_id(arn) == f"bedrock/converse/{arn}"


def test_bedrock_system_inference_profile_arn_routes_to_converse_in_litellm() -> None:
    # Guards the pin against the installed LiteLLM: the route it resolves must be Converse, not InvokeModel.
    from litellm.llms.bedrock.common_utils import BedrockModelInfo

    arn = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.openai.gpt-6.1-sol"
    assert BedrockModelInfo.get_bedrock_route(model_resolution.bedrock_litellm_model_id(arn)) == "converse"


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

    assert isinstance(model, LiteLLM)
    assert model.id == "bedrock/anthropic.claude-v1"
    assert model.get_request_params()["aws_region_name"] == "us-east-1"


def test_build_agno_model_for_bedrock_application_inference_profile() -> None:
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

    assert isinstance(model, LiteLLM)
    assert model.id == f"bedrock/converse/{profile_arn}"
    assert model.get_request_params()["aws_region_name"] == "us-east-1"


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

    assert isinstance(model, LiteLLM)
    assert model.id == "azure/chat"
    assert model.api_base == "https://example.openai.azure.com"
    assert model.api_key == "plain-key"
    assert model.get_request_params()["api_version"] == "2024-10-21"


def test_build_agno_model_for_anthropic_decrypts_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_resolution, "decrypt_value", lambda *_a, **_kw: "sk-ant-plain")
    source = _source(AnthropicModelConfig(modelId="claude-sonnet-4-5", baseUrl=None, apiKeyEncrypted="encrypted"))

    model = model_resolution.build_agno_model(source, encryption_key=b"key", azure_ad_token_provider=None)

    assert isinstance(model, LiteLLM)
    assert model.id == "anthropic/claude-sonnet-4-5"
    assert model.api_base is None
    assert model.api_key == "sk-ant-plain"


def test_build_agno_model_for_openai_uses_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_resolution, "decrypt_value", lambda *_a, **_kw: "sk-plain")
    source = _source(
        OpenAIModelConfig(modelId="gpt-4.1", baseUrl="https://proxy.example.com/v1", apiKeyEncrypted="encrypted")
    )

    model = model_resolution.build_agno_model(source, encryption_key=b"key", azure_ad_token_provider=None)

    assert model.id == "openai/gpt-4.1"
    assert model.api_base == "https://proxy.example.com/v1"
    assert model.api_key == "sk-plain"


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

    assert isinstance(model, LiteLLM)
    assert model.id == "azure/chat"
    assert model.api_base == "https://example.openai.azure.com"
    assert model.api_key is None
    params = model.get_request_params()
    assert params["api_version"] == "2024-10-21"
    assert params["azure_ad_token_provider"] is token_provider


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


def _bedrock_source_model() -> LiteLLM:
    source = _source(
        AwsBedrockModelConfig(
            awsRegion="us-east-1",
            modelIdOrArn="arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/profile-id",
            baseModelId="anthropic.claude-sonnet-5",
        )
    )
    return model_resolution.build_agno_model(source, encryption_key=b"unused", azure_ad_token_provider=None)


def _azure_source_model() -> LiteLLM:
    source = _source(
        AzureOpenAIModelConfig(
            endpoint="https://example.openai.azure.com",
            deploymentName="chat",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
        )
    )
    return model_resolution.build_agno_model(source, encryption_key=b"unused", azure_ad_token_provider=lambda: "t")


@pytest.mark.parametrize(
    "build",
    [_bedrock_source_model, _azure_source_model],
    ids=["bedrock-model-source", "azure-model-source"],
)
def test_built_models_send_no_sampling_params(build) -> None:
    # Claude Sonnet 5 on Bedrock rejects both ("`temperature` is deprecated for this model"), and
    # LiteLLM's drop_params can't drop them for an unrecognized AIP ARN / Azure deployment name.
    params = build().get_request_params(tools=[{"type": "function", "function": {"name": "t"}}])

    assert "temperature" not in params
    assert "top_p" not in params


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
@pytest.mark.parametrize("case", ["invalid", "missing", "deleted", "embedding"])
async def test_explicit_override_rejects_unusable_source(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    source = _source(AwsBedrockModelConfig(awsRegion="us-east-1", modelIdOrArn="chat", baseModelId="chat"))
    if case == "deleted":
        source.deletedAt = datetime.now(UTC)
    if case == "embedding":
        source.mode = ModelSourceMode.EMBEDDING
    source_id = "invalid" if case == "invalid" else str(source.id)
    get_source = AsyncMock(return_value=None if case == "missing" else source)
    monkeypatch.setattr(model_resolution.ModelSource, "get", get_source)
    build = MagicMock()
    monkeypatch.setattr(model_resolution, "build_agno_model", build)
    with pytest.raises(WorkflowConfigError, match=source_id):
        await model_resolution.resolve_model(source_id, encryption_key=b"key", azure_ad_token_provider=None)
    build.assert_not_called()
    if case == "invalid":
        get_source.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no-selection", "no-id", "invalid", "missing", "deleted", "embedding"])
async def test_default_returns_none_for_unusable_reference(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    source = _source(AwsBedrockModelConfig(awsRegion="us-east-1", modelIdOrArn="chat", baseModelId="chat"))
    if case == "deleted":
        source.deletedAt = datetime.now(UTC)
    if case == "embedding":
        source.mode = ModelSourceMode.EMBEDDING
    source_id = None if case == "no-id" else ("invalid" if case == "invalid" else source.id)
    selection = None if case == "no-selection" else SimpleNamespace(defaultWorkflowModelSourceId=source_id)
    monkeypatch.setattr(model_resolution, "get_model_gateway_selection", AsyncMock(return_value=selection))
    monkeypatch.setattr(
        model_resolution.ModelSource, "get", AsyncMock(return_value=None if case == "missing" else source)
    )
    assert (
        await model_resolution.resolve_default_workflow_model(encryption_key=b"key", azure_ad_token_provider=None)
        is None
    )


@pytest.mark.asyncio
async def test_resolve_default_reads_selection_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [
        _source(AwsBedrockModelConfig(awsRegion="us-east-1", modelIdOrArn=name, baseModelId=name))
        for name in ["a", "b"]
    ]
    selection = AsyncMock(side_effect=[SimpleNamespace(defaultWorkflowModelSourceId=source.id) for source in sources])
    get_source = AsyncMock(side_effect=sources)
    monkeypatch.setattr(model_resolution, "get_model_gateway_selection", selection)
    monkeypatch.setattr(model_resolution.ModelSource, "get", get_source)
    models = [
        await model_resolution.resolve_default_workflow_model(encryption_key=b"key", azure_ad_token_provider=None)
        for _ in sources
    ]
    assert [model.id for model in models] == ["bedrock/a", "bedrock/b"]
    assert all(call.kwargs == {"create_if_missing": False} for call in selection.await_args_list)
    assert [call.args[0] for call in get_source.await_args_list] == [source.id for source in sources]


@pytest.mark.asyncio
@pytest.mark.parametrize("other_field", [None, "embeddingModelSourceId"])
async def test_default_handles_only_invalid_default_id_from_real_repository(
    monkeypatch: pytest.MonkeyPatch, other_field: str | None
) -> None:
    from pydantic import ValidationError

    from registry_pkgs.models.model_gateway_selection import MODEL_GATEWAY_SELECTION_ID, ModelGatewaySelection

    doc = {"_id": MODEL_GATEWAY_SELECTION_ID, "defaultWorkflowModelSourceId": "invalid"}
    if other_field:
        doc[other_field] = "also-invalid"
    collection = SimpleNamespace(find_one=AsyncMock(return_value=doc))
    monkeypatch.setattr(ModelGatewaySelection, "get_pymongo_collection", lambda *_args: collection)
    monkeypatch.setattr(ModelGatewaySelection, "get_settings", lambda *_args: SimpleNamespace())
    if other_field:
        with pytest.raises(ValidationError):
            await model_resolution.resolve_default_workflow_model(encryption_key=b"key", azure_ad_token_provider=None)
    else:
        assert (
            await model_resolution.resolve_default_workflow_model(encryption_key=b"key", azure_ad_token_provider=None)
            is None
        )
    collection.find_one.assert_awaited_once_with({"_id": MODEL_GATEWAY_SELECTION_ID})


@pytest.mark.asyncio
@pytest.mark.parametrize("default", [False, True])
@pytest.mark.parametrize("failure", ["database", "build"])
async def test_model_resolution_propagates_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch, default: bool, failure: str
) -> None:
    from pymongo.errors import AutoReconnect

    source = _source(AwsBedrockModelConfig(awsRegion="us-east-1", modelIdOrArn="chat", baseModelId="chat"))
    monkeypatch.setattr(
        model_resolution,
        "get_model_gateway_selection",
        AsyncMock(return_value=SimpleNamespace(defaultWorkflowModelSourceId=source.id)),
    )
    error = AutoReconnect("offline") if failure == "database" else ValueError("cannot decrypt")
    monkeypatch.setattr(
        model_resolution.ModelSource,
        "get",
        AsyncMock(side_effect=error) if failure == "database" else AsyncMock(return_value=source),
    )
    monkeypatch.setattr(model_resolution, "build_agno_model", MagicMock(side_effect=error))
    with pytest.raises(type(error)) as caught:
        if default:
            await model_resolution.resolve_default_workflow_model(encryption_key=b"key", azure_ad_token_provider=None)
        else:
            await model_resolution.resolve_model(str(source.id), encryption_key=b"key", azure_ad_token_provider=None)
    assert caught.value is error
