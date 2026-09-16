from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services import model_gateway_selection_service as svc_module
from registry.services.model_gateway_selection_service import ModelGatewaySelectionService
from registry_pkgs.models.enums import ModelSourceMode

VALID_ID = "0" * 24


@pytest.fixture
def service() -> ModelGatewaySelectionService:
    return ModelGatewaySelectionService()


def _selection(**overrides) -> SimpleNamespace:
    values = {
        "defaultWorkflowModelSourceId": None,
        "embeddingModelSourceId": None,
        "updatedBy": None,
    }
    values.update(overrides)
    return SimpleNamespace(
        **values,
    )


async def test_get_selection_returns_existing_document(service, monkeypatch) -> None:
    existing = _selection()
    get_selection = AsyncMock(return_value=existing)
    monkeypatch.setattr(svc_module, "get_model_gateway_selection", get_selection)

    result = await service.get_selection()

    assert result is existing
    get_selection.assert_awaited_once_with(create_if_missing=True)


async def test_get_selection_raises_when_repository_cannot_create(service, monkeypatch) -> None:
    monkeypatch.setattr(svc_module, "get_model_gateway_selection", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="Failed to create"):
        await service.get_selection()


async def test_set_default_workflow_model_requires_chat_mode(service, monkeypatch) -> None:
    monkeypatch.setattr(
        svc_module.ModelSource,
        "get",
        AsyncMock(return_value=SimpleNamespace(mode=ModelSourceMode.EMBEDDING, deletedAt=None)),
    )
    with pytest.raises(ValueError, match="expected 'chat'"):
        await service.set_default_workflow_model(VALID_ID, updated_by="admin")


async def test_set_default_workflow_model_success(service, monkeypatch) -> None:
    selection = _selection(defaultWorkflowModelSourceId=PydanticObjectId(VALID_ID), updatedBy="admin")
    monkeypatch.setattr(
        svc_module.ModelSource,
        "get",
        AsyncMock(return_value=SimpleNamespace(mode=ModelSourceMode.CHAT, deletedAt=None)),
    )
    set_selection = AsyncMock(return_value=selection)
    monkeypatch.setattr(svc_module, "set_model_gateway_selection", set_selection)

    result = await service.set_default_workflow_model(VALID_ID, updated_by="admin")

    assert result.defaultWorkflowModelSourceId == PydanticObjectId(VALID_ID)
    assert result.updatedBy == "admin"
    set_selection.assert_awaited_once_with(
        "defaultWorkflowModelSourceId",
        PydanticObjectId(VALID_ID),
        updated_by="admin",
    )


async def test_set_embedding_model_requires_embedding_mode(service, monkeypatch) -> None:
    monkeypatch.setattr(
        svc_module.ModelSource,
        "get",
        AsyncMock(return_value=SimpleNamespace(mode=ModelSourceMode.CHAT, deletedAt=None)),
    )
    with pytest.raises(ValueError, match="expected 'embedding'"):
        await service.set_embedding_model(VALID_ID, updated_by="admin")


async def test_missing_model_source_raises(service, monkeypatch) -> None:
    monkeypatch.setattr(svc_module.ModelSource, "get", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="not found"):
        await service.set_default_workflow_model(VALID_ID, updated_by="admin")


async def test_invalid_id_raises_not_found(service) -> None:
    with pytest.raises(ValueError, match="not found"):
        await service.set_default_workflow_model("not-an-object-id", updated_by="admin")


async def test_soft_deleted_source_treated_as_missing(service, monkeypatch) -> None:
    monkeypatch.setattr(
        svc_module.ModelSource,
        "get",
        AsyncMock(return_value=SimpleNamespace(mode=ModelSourceMode.CHAT, deletedAt=object())),
    )
    with pytest.raises(ValueError, match="not found"):
        await service.set_default_workflow_model(VALID_ID, updated_by="admin")
