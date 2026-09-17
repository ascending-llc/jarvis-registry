from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import litellm
from agno.models.base import Model
from agno.models.litellm import LiteLLM
from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry_pkgs.core.crypto_utils import decrypt_value
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource

logger = logging.getLogger(__name__)

AzureAdTokenProvider = Callable[[], str]
_BEDROCK_APPLICATION_INFERENCE_PROFILE = ":application-inference-profile/"


def _bedrock_model_id(model_id_or_arn: str) -> str:
    """Return the LiteLLM route for a Bedrock model or application inference profile."""
    route = "bedrock/converse/" if _BEDROCK_APPLICATION_INFERENCE_PROFILE in model_id_or_arn else "bedrock/"
    return f"{route}{model_id_or_arn}"


def _bedrock_request_params(aws_region: str) -> dict[str, Any]:
    """LiteLLM request params for every Bedrock model built here.

    ``drop_params`` lets LiteLLM silently drop params a Bedrock model does not accept — agno always
    sends ``tool_choice="auto"`` when a node has tools (e.g. every MCP node), and Bedrock models such
    as Nova reject ``tool_choice``. Dropping it is a no-op since ``"auto"`` is the model's default.
    """
    return {"aws_region_name": aws_region, "drop_params": True}


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


def build_legacy_bedrock_model(model_id: str, aws_region: str) -> Model:
    """Build the Settings-based fallback used before a workflow ModelSource is selected."""
    return LiteLLM(
        id=_bedrock_model_id(model_id),
        request_params=_bedrock_request_params(aws_region),
    )


def get_litellm_model_info(model: str) -> dict[str, Any]:
    """Return metadata from the LiteLLM version selected by Agno's litellm extra."""
    return litellm.get_model_info(model)


def build_agno_model(
    model_source: ModelSource,
    *,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
) -> Model:
    """Build the agno Model for one ModelSource. No client/connection is constructed here."""
    config = model_source.providerConfig
    if isinstance(config, AwsBedrockModelConfig):
        return LiteLLM(
            id=_bedrock_model_id(config.modelIdOrArn),
            request_params=_bedrock_request_params(config.awsRegion),
        )
    if isinstance(config, AzureOpenAIModelConfig):
        request_params: dict[str, Any] = {"api_version": config.apiVersion}
        api_key: str | None = None
        if config.apiKeyEncrypted:
            api_key = decrypt_value(config.apiKeyEncrypted, encryption_key=encryption_key)
        else:
            if azure_ad_token_provider is None:
                raise RuntimeError("Azure Workload Identity model requires an app-scoped token provider")
            request_params["azure_ad_token_provider"] = azure_ad_token_provider
        return LiteLLM(
            id=f"azure/{config.deploymentName}",
            api_base=config.endpoint,
            api_key=api_key,
            request_params=request_params,
        )
    raise TypeError(f"Unsupported model source provider config: {type(config).__name__}")


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
