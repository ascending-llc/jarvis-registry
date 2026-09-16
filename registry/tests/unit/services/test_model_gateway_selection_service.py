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


class _StubSelection:
    """Stand-in for the Beanie ModelGatewaySelection document (avoids needing DB init in unit tests).

    `insert` is deliberately NOT set as an instance attribute here (unlike other _Stub* helpers
    in this test suite) because these tests need to control its behavior (success vs.
    DuplicateKeyError) per test via a class-level monkeypatch; an instance attribute set in
    __init__ would shadow that patch.
    """

    def __init__(self, **kwargs):
        self.defaultWorkflowModelSourceId = None
        self.embeddingModelSourceId = None
        self.updatedBy = None
        for key, value in kwargs.items():
            setattr(self, key, value)

    @staticmethod
    async def find_one(*_args, **_kwargs):
        raise NotImplementedError("tests must monkeypatch find_one")

    @staticmethod
    async def insert(*_args, **_kwargs):
        raise NotImplementedError("tests must monkeypatch insert")


async def test_get_selection_returns_existing_document(service, monkeypatch) -> None:
    existing = _selection()
    monkeypatch.setattr(svc_module.ModelGatewaySelection, "find_one", AsyncMock(return_value=existing))

    result = await service.get_selection()

    assert result is existing


async def test_get_selection_creates_document_when_absent(service, monkeypatch) -> None:
    monkeypatch.setattr(svc_module, "ModelGatewaySelection", _StubSelection)
    monkeypatch.setattr(svc_module.ModelGatewaySelection, "find_one", AsyncMock(return_value=None))
    insert_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_module.ModelGatewaySelection, "insert", insert_mock)

    result = await service.get_selection()

    assert result.id == svc_module.MODEL_GATEWAY_SELECTION_ID
    assert result.defaultWorkflowModelSourceId is None
    assert result.embeddingModelSourceId is None
    insert_mock.assert_awaited_once()


async def test_get_selection_recovers_from_concurrent_insert(service, monkeypatch) -> None:
    """A racing request that creates the singleton first should not surface DuplicateKeyError."""
    existing = _selection()
    monkeypatch.setattr(svc_module, "ModelGatewaySelection", _StubSelection)
    monkeypatch.setattr(
        svc_module.ModelGatewaySelection,
        "find_one",
        AsyncMock(side_effect=[None, existing]),
    )
    losing_insert = AsyncMock(side_effect=svc_module.DuplicateKeyError("dup"))
    monkeypatch.setattr(svc_module.ModelGatewaySelection, "insert", losing_insert)

    result = await service.get_selection()

    assert result is existing
    losing_insert.assert_awaited_once()


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
