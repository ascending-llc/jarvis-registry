from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import litellm
from agno.models.base import Model
from agno.models.litellm import LiteLLM
from beanie import PydanticObjectId
from bson.errors import InvalidId
from pydantic import ValidationError

from registry_pkgs.core.crypto_utils import decrypt_value
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_source import (
    AnthropicModelConfig,
    AwsBedrockModelConfig,
    AzureOpenAIModelConfig,
    ModelSource,
    ModelSourceProviderConfig,
    OpenAIModelConfig,
)
from registry_pkgs.telemetry.workflow_metrics import ModelPricing
from registry_pkgs.workflows.bedrock_ids import bedrock_litellm_model_id
from registry_pkgs.workflows.types import WorkflowConfigError

logger = logging.getLogger(__name__)

AzureAdTokenProvider = Callable[[], str]


def _bedrock_request_params(aws_region: str) -> dict[str, Any]:
    """LiteLLM request params for every Bedrock model built here.

    ``drop_params`` lets LiteLLM silently drop params a Bedrock model does not accept — agno always
    sends ``tool_choice="auto"`` when a node has tools (e.g. every MCP node), and Bedrock models such
    as Nova reject ``tool_choice``. Dropping it is a no-op since ``"auto"`` is the model's default.
    """
    return {"aws_region_name": aws_region, "drop_params": True}


@dataclass
class PricedLiteLLM(LiteLLM):
    """LiteLLM model that keeps the list price of its ModelSource's canonical model.

    The route a model is called by is often not a key in LiteLLM's model map: an Azure deployment
    name is arbitrary, and an inference profile ARN is not a model id. So the price is looked up
    while the ModelSource is at hand, and the model carries it for the usage metrics.
    """

    pricing: ModelPricing | None = None


def _litellm_model(
    model_id: str,
    request_params: dict[str, Any],
    *,
    api_base: str | None = None,
    api_key: str | None = None,
) -> PricedLiteLLM:
    """Build an agno LiteLLM model that sends no sampling params.

    agno's ``LiteLLM`` defaults ``temperature=0.7`` and ``top_p=1.0`` and sends both on every request,
    but newer models reject them outright (Claude Sonnet 5 on Bedrock: "`temperature` is deprecated
    for this model"). ``drop_params`` can't catch this: LiteLLM only drops params for models it can
    look up, and an application inference profile ARN or an Azure deployment name is not one of them.
    agno omits ``None`` params, so the provider's own defaults apply — as with agno's native
    ``AwsBedrock`` used before LiteLLM.
    """
    return PricedLiteLLM(
        id=model_id,
        api_base=api_base,
        api_key=api_key,
        temperature=None,
        top_p=None,
        request_params=request_params,
    )


class AzureModelCredential:
    """App-scoped Azure credential and synchronous bearer-token provider for agno's AzureOpenAI."""

    def __init__(self) -> None:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        self._credential = DefaultAzureCredential()
        self.token_provider: AzureAdTokenProvider = get_bearer_token_provider(
            self._credential,
            "https://cognitiveservices.azure.com/.default",
        )

    def close(self) -> None:
        self._credential.close()


def get_litellm_model_info(model: str) -> dict[str, Any]:
    """Return metadata from the LiteLLM version selected by Agno's litellm extra."""
    return litellm.get_model_info(model)


def litellm_metadata_key(provider_config: ModelSourceProviderConfig) -> str:
    """The id litellm.get_model_info() recognizes for this config's canonical model."""
    if isinstance(provider_config, AwsBedrockModelConfig):
        return f"bedrock/{provider_config.baseModelId}"
    if isinstance(provider_config, AzureOpenAIModelConfig):
        return f"azure/{provider_config.baseModelId}"
    # First-party: prefix with the provider so the lookup matches the runtime model string and an
    # OpenAI-compatible vendor serving e.g. a Claude id never picks up Anthropic metadata.
    return f"{provider_config.providerType}/{provider_config.modelId}"


def get_model_pricing(provider_config: ModelSourceProviderConfig) -> ModelPricing | None:
    """The list price of the config's canonical model, or None when LiteLLM has no price for it."""
    key = litellm_metadata_key(provider_config)
    try:
        info = get_litellm_model_info(key)
    except Exception as exc:  # litellm raises a bare Exception for an unmapped model
        logger.debug("No LiteLLM model metadata for %s: %s", key, exc)
        return None
    input_price = info.get("input_cost_per_token")
    output_price = info.get("output_cost_per_token")
    if input_price is None or output_price is None:
        return None
    return ModelPricing(input=input_price, output=output_price)


def build_agno_model(
    model_source: ModelSource,
    *,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
) -> Model:
    """Build the agno Model for one ModelSource. No client/connection is constructed here.

    The returned model carries the canonical model's list price in ``pricing``, for the usage metrics.
    """
    config = model_source.providerConfig
    if isinstance(config, AwsBedrockModelConfig):
        model = _litellm_model(bedrock_litellm_model_id(config.modelIdOrArn), _bedrock_request_params(config.awsRegion))
    elif isinstance(config, AzureOpenAIModelConfig):
        request_params: dict[str, Any] = {"api_version": config.apiVersion}
        api_key: str | None = None
        if config.apiKeyEncrypted:
            api_key = decrypt_value(config.apiKeyEncrypted, encryption_key=encryption_key)
        else:
            if azure_ad_token_provider is None:
                raise RuntimeError("Azure Workload Identity model requires an app-scoped token provider")
            request_params["azure_ad_token_provider"] = azure_ad_token_provider
        model = _litellm_model(
            f"azure/{config.deploymentName}",
            request_params,
            api_base=config.endpoint,
            api_key=api_key,
        )
    elif isinstance(config, (AnthropicModelConfig, OpenAIModelConfig)):
        prefix = "anthropic" if isinstance(config, AnthropicModelConfig) else "openai"
        if not config.apiKeyEncrypted:
            raise RuntimeError(f"{prefix} model source is missing its required API key")
        model = _litellm_model(
            f"{prefix}/{config.modelId}",
            {},
            api_base=config.baseUrl,
            api_key=decrypt_value(config.apiKeyEncrypted, encryption_key=encryption_key),
        )
    else:
        raise TypeError(f"Unsupported model source provider config: {type(config).__name__}")
    model.pricing = get_model_pricing(config)
    return model


async def _require_chat_source(model_source_id: str | PydanticObjectId) -> ModelSource:
    """Load an available chat source; infrastructure failures propagate unchanged."""
    try:
        object_id = PydanticObjectId(model_source_id)
    except (InvalidId, TypeError, ValueError) as exc:
        raise WorkflowConfigError(f"ModelSource '{model_source_id}' has an invalid id") from exc
    model_source = await ModelSource.get(object_id)
    if model_source is None:
        raise WorkflowConfigError(f"ModelSource '{model_source_id}' does not exist")
    if model_source.deletedAt is not None:
        raise WorkflowConfigError(f"ModelSource '{model_source_id}' is deleted")
    if model_source.mode != ModelSourceMode.CHAT:
        raise WorkflowConfigError(f"ModelSource '{model_source_id}' is not in chat mode")
    return model_source


async def resolve_model(
    model_source_id: str | PydanticObjectId,
    *,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
) -> Model:
    """Resolve an explicit node override, failing on an unusable reference."""
    model_source = await _require_chat_source(model_source_id)
    return build_agno_model(
        model_source,
        encryption_key=encryption_key,
        azure_ad_token_provider=azure_ad_token_provider,
    )


async def resolve_default_workflow_model(
    *,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
) -> Model | None:
    """Read the default fresh for each registry build; unusable references return None."""
    try:
        selection = await get_model_gateway_selection(create_if_missing=False)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        if not errors or not all(
            error["loc"] == ("defaultWorkflowModelSourceId",) and error["type"] == "value_error" for error in errors
        ):
            raise
        logger.warning("Default workflow ModelSource has an invalid id")
        return None
    default_id = selection.defaultWorkflowModelSourceId if selection else None
    if default_id is None:
        return None
    try:
        model_source = await _require_chat_source(default_id)
    except WorkflowConfigError as exc:
        logger.warning("No usable default workflow model: %s", exc)
        return None
    return build_agno_model(
        model_source,
        encryption_key=encryption_key,
        azure_ad_token_provider=azure_ad_token_provider,
    )
