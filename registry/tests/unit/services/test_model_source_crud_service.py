from datetime import UTC, datetime
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
from registry_pkgs.models.enums import ModelSourceMode, ModelSourceProviderType
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig
from registry_pkgs.models.workflow import WorkflowNode

VALID_ID = "0" * 24


class _FakeFinder:
    def __init__(self, items):
        self._items = items

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self):
        return self._items

    async def count(self):
        return len(self._items)

    def __aiter__(self):
        async def _iterate():
            for item in self._items:
                yield item

        return _iterate()


class _StubModelSource:
    """Stand-in for the Beanie ModelSource document (avoids needing DB init in unit tests)."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.insert = AsyncMock()


@pytest.fixture
def selection_service() -> MagicMock:
    mock = MagicMock()
    mock.get_selection = AsyncMock(
        return_value=SimpleNamespace(defaultWorkflowModelSourceId=None, embeddingModelSourceId=None)
    )
    return mock


@pytest.fixture
def service(selection_service: MagicMock, monkeypatch: pytest.MonkeyPatch) -> ModelSourceCrudService:
    monkeypatch.setattr(crud_module.WorkflowDefinition, "find", lambda *_a, **_kw: _FakeFinder([]))
    return ModelSourceCrudService(model_gateway_selection_service=selection_service)


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


async def test_create_source_inserts_and_returns_source(service, monkeypatch) -> None:
    monkeypatch.setattr(crud_module, "ModelSource", _StubModelSource)
    provider_config = AwsBedrockModelConfigInput(awsRegion="us-east-1", modelIdOrArn="m", baseModelId="m")

    source = await service.create_source(
        display_name="Claude Sonnet",
        description="desc",
        tags=["chat"],
        mode=ModelSourceMode.CHAT,
        provider_config=provider_config,
        created_by="user-1",
    )

    assert source.displayName == "Claude Sonnet"
    assert source.createdBy == "user-1"
    assert source.updatedBy == "user-1"
    assert isinstance(source.providerConfig, AwsBedrockModelConfig)
    source.insert.assert_awaited_once()


async def test_get_source_returns_none_for_invalid_id(service) -> None:
    assert await service.get_source("not-an-object-id") is None


async def test_get_source_returns_none_when_not_found(service, monkeypatch) -> None:
    monkeypatch.setattr(crud_module.ModelSource, "get", AsyncMock(return_value=None))
    assert await service.get_source(str(PydanticObjectId())) is None


async def test_get_source_returns_none_when_soft_deleted(service, monkeypatch) -> None:
    stub = SimpleNamespace(deletedAt=datetime.now(UTC))
    monkeypatch.setattr(crud_module.ModelSource, "get", AsyncMock(return_value=stub))
    assert await service.get_source(str(PydanticObjectId())) is None


async def test_get_source_returns_source_when_active(service, monkeypatch) -> None:
    stub = SimpleNamespace(deletedAt=None)
    monkeypatch.setattr(crud_module.ModelSource, "get", AsyncMock(return_value=stub))
    assert await service.get_source(str(PydanticObjectId())) is stub


async def test_list_sources_returns_items_and_count(service, monkeypatch) -> None:
    items = [SimpleNamespace(), SimpleNamespace()]
    monkeypatch.setattr(crud_module.ModelSource, "find", lambda *_a, **_kw: _FakeFinder(items))

    result_items, total = await service.list_sources()

    assert result_items == items
    assert total == 2


async def test_list_sources_applies_mode_provider_tag_and_keyword_filters(service, monkeypatch) -> None:
    captured = {}

    def _find(query):
        captured["query"] = query
        return _FakeFinder([])

    monkeypatch.setattr(crud_module.ModelSource, "find", _find)

    await service.list_sources(
        mode=ModelSourceMode.CHAT,
        provider_type=ModelSourceProviderType.AWS_BEDROCK,
        tag="prod",
        keyword="sonnet",
    )

    filters = captured["query"]["$and"]
    assert {"mode": "chat"} in filters
    assert {"providerConfig.providerType": "aws_bedrock"} in filters
    assert {"tags": "prod"} in filters
    assert {"$text": {"$search": "sonnet"}} in filters


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


async def test_update_rejects_mode_change_when_in_use(service, selection_service) -> None:
    sid = PydanticObjectId(VALID_ID)
    source = SimpleNamespace(id=sid, mode=ModelSourceMode.CHAT, updatedBy=None, save=AsyncMock())
    selection_service.get_selection.return_value = SimpleNamespace(
        defaultWorkflowModelSourceId=sid, embeddingModelSourceId=None
    )
    with pytest.raises(ValueError, match="in use"):
        await service.update_source(source, {"mode": ModelSourceMode.EMBEDDING}, updated_by="admin")
    source.save.assert_not_called()


async def test_update_allows_mode_change_when_not_in_use(service, selection_service) -> None:
    source = SimpleNamespace(id=PydanticObjectId(VALID_ID), mode=ModelSourceMode.CHAT, updatedBy=None, save=AsyncMock())
    selection_service.get_selection.return_value = SimpleNamespace(
        defaultWorkflowModelSourceId=None, embeddingModelSourceId=None
    )
    await service.update_source(source, {"mode": ModelSourceMode.EMBEDDING}, updated_by="admin")
    assert source.mode == ModelSourceMode.EMBEDDING
    source.save.assert_awaited_once()


async def test_is_in_use_true_when_workflow_default(service, selection_service) -> None:
    selection_service.get_selection.return_value = SimpleNamespace(
        defaultWorkflowModelSourceId=PydanticObjectId(VALID_ID),
        embeddingModelSourceId=None,
    )
    assert await service.is_in_use(VALID_ID) is True


async def test_is_in_use_false_when_unreferenced(service, selection_service) -> None:
    selection_service.get_selection.return_value = SimpleNamespace(
        defaultWorkflowModelSourceId=None, embeddingModelSourceId=None
    )
    assert await service.is_in_use(VALID_ID) is False


async def test_is_in_use_true_when_workflow_node_references_source(service, selection_service, monkeypatch) -> None:
    selection_service.get_selection.return_value = SimpleNamespace(
        defaultWorkflowModelSourceId=None, embeddingModelSourceId=None
    )
    definition = SimpleNamespace(
        nodes=[WorkflowNode(name="step", executor_key="tool", model_source_id=VALID_ID, step_objective="run")]
    )
    monkeypatch.setattr(crud_module.WorkflowDefinition, "find", lambda *_a, **_kw: _FakeFinder([definition]))

    assert await service.is_in_use(VALID_ID) is True


async def test_is_in_use_normalizes_workflow_model_source_id(service, selection_service, monkeypatch) -> None:
    source_id = "abcdefabcdefabcdefabcdef"
    selection_service.get_selection.return_value = SimpleNamespace(
        defaultWorkflowModelSourceId=None,
        embeddingModelSourceId=None,
    )
    definition = SimpleNamespace(
        nodes=[
            WorkflowNode(
                name="step",
                executor_key="tool",
                model_source_id=source_id.upper(),
                step_objective="run",
            )
        ]
    )
    monkeypatch.setattr(crud_module.WorkflowDefinition, "find", lambda *_a, **_kw: _FakeFinder([definition]))

    assert await service.is_in_use(source_id) is True


async def test_is_in_use_false_for_invalid_id_without_querying_selection(service, selection_service) -> None:
    assert await service.is_in_use("not-an-object-id") is False
    selection_service.get_selection.assert_not_awaited()


async def test_soft_delete_sets_deleted_at_and_saves(service) -> None:
    source = SimpleNamespace(deletedAt=None, save=AsyncMock())

    result = await service.soft_delete(source)

    assert result.deletedAt is not None
    source.save.assert_awaited_once()
