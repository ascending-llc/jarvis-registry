import logging

from registry_pkgs.models.model_source import (
    AwsBedrockModelConfig,
    AzureOpenAIModelConfig,
    ModelSourceProviderConfig,
)
from registry_pkgs.workflows.model_resolution import get_litellm_model_info

from ..schemas.model_source_api_schemas import ModelSourceMetadataResponse

logger = logging.getLogger(__name__)


def _litellm_metadata_key(provider_config: ModelSourceProviderConfig) -> str:
    """The id litellm.get_model_info() recognizes for this config's canonical model."""
    if isinstance(provider_config, AwsBedrockModelConfig):
        return f"bedrock/{provider_config.baseModelId}"
    if isinstance(provider_config, AzureOpenAIModelConfig):
        return f"azure/{provider_config.baseModelId}"
    # First-party: the model id is itself litellm's canonical key, no prefix needed.
    return provider_config.modelId


def get_model_metadata(
    provider_config: ModelSourceProviderConfig,
) -> ModelSourceMetadataResponse:
    litellm_model_string = _litellm_metadata_key(provider_config)
    try:
        info = get_litellm_model_info(litellm_model_string)
    except Exception as exc:  # litellm raises a bare Exception for an unmapped model
        logger.warning("No litellm model metadata for %s: %s", litellm_model_string, exc)
        return ModelSourceMetadataResponse(unavailableReason=str(exc))
    return ModelSourceMetadataResponse(
        maxInputTokens=info.get("max_input_tokens"),
        maxOutputTokens=info.get("max_output_tokens"),
        inputCostPerToken=info.get("input_cost_per_token"),
        outputCostPerToken=info.get("output_cost_per_token"),
        supportsPromptCaching=info.get("supports_prompt_caching"),
    )
