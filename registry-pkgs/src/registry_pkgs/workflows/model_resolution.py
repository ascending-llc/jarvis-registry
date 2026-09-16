from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from agno.models.base import Model
from agno.models.litellm import LiteLLM
from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry_pkgs.core.crypto_utils import decrypt_value
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_source import AwsBedrockModelConfig, ModelSource

logger = logging.getLogger(__name__)

BEDROCK_APPLICATION_INFERENCE_PROFILE_MARKER = ":application-inference-profile/"

AzureAdTokenProvider = Callable[[], str]


class AzureModelCredential:
    """App-scoped Azure credential and synchronous bearer-token provider for LiteLLM."""

    def __init__(self) -> None:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        self._credential = DefaultAzureCredential()
        self.token_provider: AzureAdTokenProvider = get_bearer_token_provider(
            self._credential,
            "https://cognitiveservices.azure.com/.default",
        )

    def close(self) -> None:
        self._credential.close()


def build_agno_model(
    model_source: ModelSource,
    *,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
) -> Model:
    """Build the agno Model for one ModelSource. No client/connection is constructed here."""
    config = model_source.providerConfig
    if isinstance(config, AwsBedrockModelConfig):
        route = (
            "bedrock/converse/" if BEDROCK_APPLICATION_INFERENCE_PROFILE_MARKER in config.modelIdOrArn else "bedrock/"
        )
        return LiteLLM(
            id=f"{route}{config.modelIdOrArn}",
            request_params={"aws_region_name": config.awsRegion},
        )

    # AzureOpenAIModelConfig
    request_params: dict[str, Any] = {"api_version": config.apiVersion}
    kwargs: dict[str, Any] = {"id": f"azure/{config.deploymentName}", "api_base": config.endpoint}
    if config.apiKeyEncrypted:
        kwargs["api_key"] = decrypt_value(config.apiKeyEncrypted, encryption_key=encryption_key)
    else:
        if azure_ad_token_provider is None:
            raise RuntimeError("Azure Workload Identity model requires an app-scoped token provider")
        request_params["azure_ad_token_provider"] = azure_ad_token_provider
    kwargs["request_params"] = request_params
    return LiteLLM(**kwargs)


async def resolve_model(
    model_source_id: str | PydanticObjectId | None,
    *,
    fallback_model: Model,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
) -> Model:
    """Resolve one ModelSource id to an agno Model, or fall back. Resolved fresh on every call."""
    if model_source_id is None:
        return fallback_model
    try:
        object_id = PydanticObjectId(model_source_id)
    except (InvalidId, TypeError, ValueError):
        logger.warning("ModelSource %s has an invalid id; using fallback model", model_source_id)
        return fallback_model
    model_source = await ModelSource.get(object_id)
    if model_source is None or model_source.deletedAt is not None or model_source.mode != ModelSourceMode.CHAT:
        logger.warning("ModelSource %s is unavailable for chat; using fallback model", model_source_id)
        return fallback_model
    return build_agno_model(
        model_source,
        encryption_key=encryption_key,
        azure_ad_token_provider=azure_ad_token_provider,
    )


async def resolve_default_workflow_model(
    *,
    fallback_model: Model,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
) -> Model:
    """Resolve the currently-configured default workflow model.

    Falls back to the legacy Settings-based model when no ModelSource has been set as default yet
    (fresh deployment / pre-migration).
    """
    selection = await get_model_gateway_selection(create_if_missing=False)
    default_id = selection.defaultWorkflowModelSourceId if selection else None
    return await resolve_model(
        default_id,
        fallback_model=fallback_model,
        encryption_key=encryption_key,
        azure_ad_token_provider=azure_ad_token_provider,
    )
