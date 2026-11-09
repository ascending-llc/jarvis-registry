import logging

import pytest
from beanie import PydanticObjectId
from skill_scan_worker import llm as llm_module
from skill_scan_worker.config import settings
from skill_scan_worker.llm import resolve_llm

from registry_pkgs.core.crypto_utils import encrypt_value
from registry_pkgs.models.enums import ModelSourceMode, ModelSourceProviderType
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource

pytestmark = pytest.mark.asyncio


def _bedrock(model_id_or_arn: str) -> ModelSource:
    source = ModelSource(
        displayName="scan",
        mode=ModelSourceMode.CHAT,
        providerConfig=AwsBedrockModelConfig(
            awsRegion="us-east-1", modelIdOrArn=model_id_or_arn, baseModelId="anthropic.claude-3-5-sonnet"
        ),
    )
    source.id = PydanticObjectId()
    return source


def _azure(api_key_encrypted: str | None) -> ModelSource:
    source = ModelSource(
        displayName="scan",
        mode=ModelSourceMode.CHAT,
        providerConfig=AzureOpenAIModelConfig(
            endpoint="https://x.openai.azure.com",
            deploymentName="gpt-4o-scan",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
            apiKeyEncrypted=api_key_encrypted,
        ),
    )
    source.id = PydanticObjectId()
    return source


async def test_bedrock_model_id() -> None:
    resolved = await resolve_llm(_bedrock("anthropic.claude-3-5-sonnet-20240620-v1:0"))
    assert resolved is not None
    assert resolved.provider == ModelSourceProviderType.AWS_BEDROCK
    assert resolved.bedrock_model == "anthropic.claude-3-5-sonnet-20240620-v1:0"
    assert resolved.aws_region == "us-east-1"


async def test_bedrock_arn() -> None:
    arn = "arn:aws:bedrock:us-east-1:123:application-inference-profile/abc"
    resolved = await resolve_llm(_bedrock(arn))
    assert resolved is not None
    assert resolved.bedrock_model == arn


async def test_azure_with_key_is_decrypted() -> None:
    encrypted = encrypt_value("super-secret-key", encryption_key=settings.encryption_key)
    resolved = await resolve_llm(_azure(encrypted))
    assert resolved is not None
    assert resolved.provider == ModelSourceProviderType.AZURE_OPENAI
    assert resolved.azure_api_key is not None
    assert resolved.azure_api_key.get_secret_value() == "super-secret-key"


async def test_azure_without_key_uses_workload_identity() -> None:
    resolved = await resolve_llm(_azure(None))
    assert resolved is not None
    assert resolved.azure_api_key is None


async def test_decryption_failure_yields_none_and_warns_without_the_value(caplog, monkeypatch) -> None:
    encrypted = encrypt_value("super-secret-key", encryption_key=settings.encryption_key)

    def _fail(*_args, **_kwargs):
        raise Exception("Failed to decrypt value")  # decrypt_value raises a plain Exception

    monkeypatch.setattr(llm_module, "decrypt_value", _fail)
    with caplog.at_level(logging.WARNING, logger="skill_scan_worker.llm"):
        assert await resolve_llm(_azure(encrypted)) is None

    warnings = [r for r in caplog.records if r.name == "skill_scan_worker.llm" and r.levelno == logging.WARNING]
    assert warnings
    assert "super-secret-key" not in caplog.text
    assert encrypted not in caplog.text


async def test_malformed_ciphertext_yields_none() -> None:
    # "zz:zz" splits into two parts, so decrypt_value tries bytes.fromhex("zz") and raises.
    assert await resolve_llm(_azure("zz:zz")) is None


async def test_none_source_yields_none() -> None:
    assert await resolve_llm(None) is None
