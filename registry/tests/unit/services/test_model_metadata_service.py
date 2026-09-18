from registry.services import model_metadata_service
from registry.services.model_metadata_service import get_model_metadata
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig


def _bedrock() -> AwsBedrockModelConfig:
    return AwsBedrockModelConfig(
        awsRegion="us-east-1",
        modelIdOrArn="anthropic.claude-3-5-sonnet",
        baseModelId="anthropic.claude-3-5-sonnet",
    )


def _azure() -> AzureOpenAIModelConfig:
    return AzureOpenAIModelConfig(
        endpoint="https://acme.openai.azure.com",
        deploymentName="d",
        baseModelId="gpt-4o",
        apiVersion="2024-10-21",
    )


def test_mapped_model_returns_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        model_metadata_service,
        "get_litellm_model_info",
        lambda model: {
            "max_input_tokens": 200000,
            "max_output_tokens": 8192,
            "input_cost_per_token": 3e-06,
            "output_cost_per_token": 1.5e-05,
            "supports_prompt_caching": True,
        },
    )
    meta = get_model_metadata(_bedrock())
    assert meta.maxInputTokens == 200000
    assert meta.supportsPromptCaching is True
    assert meta.unavailableReason is None


def test_uses_azure_prefix_for_azure_config(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def _capture(model: str):
        seen["model"] = model
        return {"max_input_tokens": 128000}

    monkeypatch.setattr(model_metadata_service, "get_litellm_model_info", _capture)
    get_model_metadata(_azure())
    assert seen["model"] == "azure/gpt-4o"


def test_unmapped_model_degrades_to_reason(monkeypatch) -> None:
    def _raise(model: str):
        raise Exception("This model isn't mapped yet")

    monkeypatch.setattr(model_metadata_service, "get_litellm_model_info", _raise)
    meta = get_model_metadata(_bedrock())
    assert meta.maxInputTokens is None
    assert meta.unavailableReason == "This model isn't mapped yet"
