from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.schemas.model_source_api_schemas import (
    AwsBedrockModelConfigInput,
    AzureOpenAIModelConfigInput,
)
from registry.services import model_source_crud_service as crud_module
from registry.services.model_source_crud_service import ModelSourceCrudService
from registry_pkgs.core.crypto_utils import is_encrypted
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig

VALID_ID = "0" * 24


@pytest.fixture
def service() -> ModelSourceCrudService:
    return ModelSourceCrudService(model_gateway_selection_service=MagicMock())


def test_to_stored_config_bedrock_has_no_secret(service) -> None:
    stored = service._to_stored_config(
        AwsBedrockModelConfigInput(awsRegion="us-east-1", modelIdOrArn="m", baseModelId="m")
    )
    assert isinstance(stored, AwsBedrockModelConfig)


def test_to_stored_config_encrypts_azure_api_key(service) -> None:
    stored = service._to_stored_config(
        AzureOpenAIModelConfigInput(
            endpoint="https://acme.openai.azure.com",
            deploymentName="d",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
            apiKey="super-secret",
        )
    )
    assert isinstance(stored, AzureOpenAIModelConfig)
    assert stored.apiKeyEncrypted is not None
    assert stored.apiKeyEncrypted != "super-secret"
    assert is_encrypted(stored.apiKeyEncrypted)


def test_to_stored_config_azure_without_key_leaves_none(service) -> None:
    stored = service._to_stored_config(
        AzureOpenAIModelConfigInput(
            endpoint="https://acme.openai.azure.com",
            deploymentName="d",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
        )
    )
    assert stored.apiKeyEncrypted is None


def test_encrypt_secret_is_idempotent(service) -> None:
    once = service._encrypt_secret("secret")
    twice = service._encrypt_secret(once)
    assert twice == once


async def test_update_source_applies_only_supplied_fields(service) -> None:
    source = SimpleNamespace(
        displayName="old",
        description="old",
        tags=[],
        mode="chat",
        providerConfig=object(),
        updatedBy=None,
        save=AsyncMock(),
    )
    await service.update_source(source, {"displayName": "new"}, updated_by="admin")
    assert source.displayName == "new"
    assert source.description == "old"
    assert source.updatedBy == "admin"
    source.save.assert_awaited_once()


async def test_update_azure_config_without_key_preserves_stored_secret(service) -> None:
    """AS-1851: omitting apiKey on update must not wipe the stored encrypted key."""
    source = SimpleNamespace(
        providerConfig=AzureOpenAIModelConfig(
            endpoint="https://acme.openai.azure.com",
            deploymentName="d",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
            apiKeyEncrypted="iv:existing-cipher",
        ),
        updatedBy=None,
        save=AsyncMock(),
    )
    new_config = AzureOpenAIModelConfigInput(
        endpoint="https://acme.openai.azure.com",
        deploymentName="d-renamed",
        baseModelId="gpt-4o",
        apiVersion="2024-10-21",
    )
    await service.update_source(source, {"providerConfig": new_config}, updated_by="admin")
    assert source.providerConfig.deploymentName == "d-renamed"
    assert source.providerConfig.apiKeyEncrypted == "iv:existing-cipher"


async def test_update_azure_config_with_new_key_reencrypts(service) -> None:
    source = SimpleNamespace(
        providerConfig=AzureOpenAIModelConfig(
            endpoint="https://acme.openai.azure.com",
            deploymentName="d",
            baseModelId="gpt-4o",
            apiVersion="2024-10-21",
            apiKeyEncrypted="iv:existing-cipher",
        ),
        updatedBy=None,
        save=AsyncMock(),
    )
    new_config = AzureOpenAIModelConfigInput(
        endpoint="https://acme.openai.azure.com",
        deploymentName="d",
        baseModelId="gpt-4o",
        apiVersion="2024-10-21",
        apiKey="brand-new-key",
    )
    await service.update_source(source, {"providerConfig": new_config}, updated_by="admin")
    enc = source.providerConfig.apiKeyEncrypted
    assert enc is not None and enc != "iv:existing-cipher" and enc != "brand-new-key"
    assert is_encrypted(enc)


async def test_is_in_use_true_when_workflow_default(service, monkeypatch) -> None:
    selection = SimpleNamespace(
        defaultWorkflowModelSourceId=PydanticObjectId(VALID_ID),
        embeddingModelSourceId=None,
    )
    monkeypatch.setattr(crud_module.ModelGatewaySelection, "find_one", AsyncMock(return_value=selection))
    assert await service.is_in_use(VALID_ID) is True


async def test_is_in_use_false_when_unreferenced(service, monkeypatch) -> None:
    selection = SimpleNamespace(defaultWorkflowModelSourceId=None, embeddingModelSourceId=None)
    monkeypatch.setattr(crud_module.ModelGatewaySelection, "find_one", AsyncMock(return_value=selection))
    assert await service.is_in_use(VALID_ID) is False


async def test_is_in_use_false_when_no_selection(service, monkeypatch) -> None:
    monkeypatch.setattr(crud_module.ModelGatewaySelection, "find_one", AsyncMock(return_value=None))
    assert await service.is_in_use(VALID_ID) is False
