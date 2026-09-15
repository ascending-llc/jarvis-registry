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


def _selection() -> SimpleNamespace:
    return SimpleNamespace(
        defaultWorkflowModelSourceId=None,
        embeddingModelSourceId=None,
        updatedBy=None,
        save=AsyncMock(),
    )


async def test_set_default_workflow_model_requires_chat_mode(service, monkeypatch) -> None:
    monkeypatch.setattr(
        svc_module.ModelSource,
        "get",
        AsyncMock(return_value=SimpleNamespace(mode=ModelSourceMode.EMBEDDING, deletedAt=None)),
    )
    with pytest.raises(ValueError, match="expected 'chat'"):
        await service.set_default_workflow_model(VALID_ID, updated_by="admin")


async def test_set_default_workflow_model_success(service, monkeypatch) -> None:
    selection = _selection()
    monkeypatch.setattr(
        svc_module.ModelSource,
        "get",
        AsyncMock(return_value=SimpleNamespace(mode=ModelSourceMode.CHAT, deletedAt=None)),
    )
    monkeypatch.setattr(svc_module.ModelGatewaySelection, "find_one", AsyncMock(return_value=selection))

    result = await service.set_default_workflow_model(VALID_ID, updated_by="admin")

    assert result.defaultWorkflowModelSourceId == PydanticObjectId(VALID_ID)
    assert result.updatedBy == "admin"
    selection.save.assert_awaited_once()


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
