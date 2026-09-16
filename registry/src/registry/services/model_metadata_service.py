import logging

import litellm

from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig

from ..schemas.model_source_api_schemas import ModelSourceMetadataResponse

logger = logging.getLogger(__name__)


def get_model_metadata(
    provider_config: AwsBedrockModelConfig | AzureOpenAIModelConfig,
) -> ModelSourceMetadataResponse:
    litellm_model_string = (
        f"bedrock/{provider_config.baseModelId}"
        if isinstance(provider_config, AwsBedrockModelConfig)
        else f"azure/{provider_config.baseModelId}"
    )
    try:
        info = litellm.get_model_info(litellm_model_string)
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
