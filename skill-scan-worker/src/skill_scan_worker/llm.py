"""Resolve the configured scan model into credentials the adapters consume.

Maps ``AwsBedrockModelConfig`` / ``AzureOpenAIModelConfig`` field by field. Whether a given adapter can use a given
``ResolvedLLM`` is the adapter's decision; for example SkillSpector cannot use Azure Workload Identity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import SecretStr

from registry_pkgs.core.crypto_utils import decrypt_value
from registry_pkgs.models.enums import ModelSourceProviderType
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource
from registry_pkgs.models.skill_scan_result import ScanLLMInfo
from registry_pkgs.skill_scan.plan import scan_llm_info
from skill_scan_worker.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedLLM:
    info: ScanLLMInfo
    provider: ModelSourceProviderType  # AWS_BEDROCK | AZURE_OPENAI
    base_model_id: str
    aws_region: str | None = None
    bedrock_model: str | None = None  # modelIdOrArn
    azure_endpoint: str | None = None
    azure_deployment: str | None = None
    azure_api_version: str | None = None
    azure_api_key: SecretStr | None = None  # None => Azure Workload Identity


async def resolve_llm(source: ModelSource | None) -> ResolvedLLM | None:
    """Resolve credentials for the configured scan model. ``None`` source, or a decryption failure, yields ``None``."""
    info = scan_llm_info(source)
    if source is None or info is None:
        return None
    config = source.providerConfig
    if isinstance(config, AwsBedrockModelConfig):
        return ResolvedLLM(
            info=info,
            provider=ModelSourceProviderType.AWS_BEDROCK,
            base_model_id=config.baseModelId,
            aws_region=config.awsRegion,
            bedrock_model=config.modelIdOrArn,
        )
    if isinstance(config, AzureOpenAIModelConfig):
        api_key: SecretStr | None = None
        if config.apiKeyEncrypted:
            try:
                api_key = SecretStr(decrypt_value(config.apiKeyEncrypted, encryption_key=settings.encryption_key))
            except Exception:
                # Never log the value; adapters then record LLM_UNAVAILABLE. decrypt_value raises a plain Exception.
                logger.warning("Failed to decrypt scan model API key for source %s", source.id, exc_info=True)
                return None
        return ResolvedLLM(
            info=info,
            provider=ModelSourceProviderType.AZURE_OPENAI,
            base_model_id=config.baseModelId,
            azure_endpoint=config.endpoint,
            azure_deployment=config.deploymentName,
            azure_api_version=config.apiVersion,
            azure_api_key=api_key,
        )
    return None  # pragma: no cover — discriminated union is exhaustive
