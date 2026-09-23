from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.api.v1.model_source import model_source_routes
from registry.api.v1.model_source.model_source_routes import router
from registry.auth.dependencies import get_current_user
from registry.core.config import settings
from registry.deps import (
    get_embedding_reindex_job_service,
    get_model_gateway_selection_service,
    get_model_source_crud_service,
)
from registry.schemas.model_source_api_schemas import ModelSourceMetadataResponse
from registry.services.embedding_reindex_job_service import (
    EmbeddingModelSmokeTestError,
    EmbeddingReindexAlreadyRunningError,
)
from registry.services.model_gateway_selection_service import (
    ModelSourceModeMismatchError,
    ModelSourceNotFoundError,
)
from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig

USER_ID = "000000000000000000000111"

_BEDROCK_BODY = {
    "displayName": "Claude Sonnet",
    "mode": "chat",
    "providerConfig": {
        "providerType": "aws_bedrock",
        "awsRegion": "us-east-1",
        "modelIdOrArn": "anthropic.claude-3-5-sonnet",
        "baseModelId": "anthropic.claude-3-5-sonnet",
    },
}


def _make_source(**overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": PydanticObjectId(),
        "displayName": "Claude Sonnet",
        "description": None,
        "tags": [],
        "mode": ModelSourceMode.CHAT,
        "providerConfig": AwsBedrockModelConfig(
            awsRegion="us-east-1",
            modelIdOrArn="anthropic.claude-3-5-sonnet",
            baseModelId="anthropic.claude-3-5-sonnet",
        ),
        "createdBy": USER_ID,
        "updatedBy": USER_ID,
        "createdAt": now,
        "updatedAt": now,
        "deletedAt": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def ctx():
    app = FastAPI()
    app.include_router(router)
    source = _make_source()

    crud = MagicMock()
    crud.create_source = AsyncMock(return_value=source)
    crud.get_source = AsyncMock(return_value=source)
    crud.list_sources = AsyncMock(return_value=([source], 1))
    crud.update_source = AsyncMock(return_value=source)
    crud.is_in_use = AsyncMock(return_value=False)
    crud.soft_delete = AsyncMock(return_value=SimpleNamespace(id=source.id, deletedAt=datetime.now(UTC)))

    selection = MagicMock()
    selection.get_selection = AsyncMock(
        return_value=SimpleNamespace(defaultWorkflowModelSourceId=None, embeddingModelSourceId=None)
    )
    selection.set_default_workflow_model = AsyncMock(
        return_value=SimpleNamespace(
            defaultWorkflowModelSourceId=source.id,
            embeddingModelSourceId=None,
        )
    )
    selection.set_embedding_model = AsyncMock(
        return_value=SimpleNamespace(
            defaultWorkflowModelSourceId=None,
            embeddingModelSourceId=source.id,
        )
    )

    reindex = MagicMock()
    reindex.trigger_reindex = AsyncMock(
        return_value=SimpleNamespace(
            defaultWorkflowModelSourceId=None,
            embeddingModelSourceId=source.id,
        )
    )

    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER_ID}
    app.dependency_overrides[get_model_source_crud_service] = lambda: crud
    app.dependency_overrides[get_model_gateway_selection_service] = lambda: selection
    app.dependency_overrides[get_embedding_reindex_job_service] = lambda: reindex

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, source=source, crud=crud, selection=selection, reindex=reindex)


def test_create_returns_detail(ctx) -> None:
    response = ctx.client.post("/model-sources", json=_BEDROCK_BODY)
    assert response.status_code == 201
    body = response.json()
    assert body["providerType"] == "aws_bedrock"
    assert body["providerConfig"]["providerType"] == "aws_bedrock"
    ctx.crud.create_source.assert_awaited_once()


def test_create_azure_response_masks_secret(ctx) -> None:
    ctx.crud.create_source = AsyncMock(
        return_value=_make_source(
            mode=ModelSourceMode.EMBEDDING,
            providerConfig=AzureOpenAIModelConfig(
                endpoint="https://acme.openai.azure.com",
                deploymentName="d",
                baseModelId="text-embedding-3-small",
                apiVersion="2024-10-21",
                apiKeyEncrypted="iv:ciphertext",
            ),
        )
    )
    body = {
        "displayName": "Embed",
        "mode": "embedding",
        "providerConfig": {
            "providerType": "azure_openai",
            "endpoint": "https://acme.openai.azure.com",
            "deploymentName": "d",
            "baseModelId": "text-embedding-3-small",
            "apiVersion": "2024-10-21",
            "apiKey": "super-secret",
        },
    }
    response = ctx.client.post("/model-sources", json=body)
    assert response.status_code == 201
    provider = response.json()["providerConfig"]
    assert provider["hasApiKey"] is True
    assert "apiKeyEncrypted" not in provider
    assert "apiKey" not in provider


def test_list_returns_paged(ctx) -> None:
    response = ctx.client.get("/model-sources")
    assert response.status_code == 200
    body = response.json()
    assert len(body["modelSources"]) == 1
    assert body["pagination"]["total"] == 1


def test_get_detail_includes_metadata(ctx, monkeypatch) -> None:
    monkeypatch.setattr(
        model_source_routes,
        "get_model_metadata",
        lambda config: ModelSourceMetadataResponse(maxInputTokens=200000),
    )
    response = ctx.client.get(f"/model-sources/{ctx.source.id}")
    assert response.status_code == 200
    assert response.json()["metadata"]["maxInputTokens"] == 200000


def test_get_missing_returns_404(ctx) -> None:
    ctx.crud.get_source = AsyncMock(return_value=None)
    response = ctx.client.get(f"/model-sources/{PydanticObjectId()}")
    assert response.status_code == 404


def test_patch_updates(ctx) -> None:
    response = ctx.client.patch(f"/model-sources/{ctx.source.id}", json={"displayName": "Renamed"})
    assert response.status_code == 200
    ctx.crud.update_source.assert_awaited_once()


def test_patch_rejects_explicit_null_on_non_nullable_field(ctx) -> None:
    for field in ("displayName", "tags", "mode", "providerConfig"):
        response = ctx.client.patch(f"/model-sources/{ctx.source.id}", json={field: None})
        assert response.status_code == 422, field
    ctx.crud.update_source.assert_not_called()


def test_patch_allows_null_description(ctx) -> None:
    response = ctx.client.patch(f"/model-sources/{ctx.source.id}", json={"description": None})
    assert response.status_code == 200


def test_patch_mode_change_in_use_returns_409(ctx) -> None:
    ctx.crud.update_source = AsyncMock(side_effect=ValueError("mode change not allowed while in use"))
    response = ctx.client.patch(f"/model-sources/{ctx.source.id}", json={"mode": "embedding"})
    assert response.status_code == 409


def test_delete_soft_deletes(ctx) -> None:
    response = ctx.client.delete(f"/model-sources/{ctx.source.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["deletedAt"] is not None
    ctx.crud.soft_delete.assert_awaited_once()


def test_delete_blocked_when_in_use(ctx) -> None:
    ctx.crud.is_in_use = AsyncMock(return_value=True)
    response = ctx.client.delete(f"/model-sources/{ctx.source.id}")
    assert response.status_code == 409
    ctx.crud.soft_delete.assert_not_called()


def test_get_selection(ctx) -> None:
    response = ctx.client.get("/model-gateway/selection")
    assert response.status_code == 200
    body = response.json()
    assert body["defaultWorkflowModelSourceId"] is None
    assert body["embeddingModelSourceId"] is None


def test_set_default_workflow_model(ctx) -> None:
    response = ctx.client.put(
        "/model-gateway/selection/default-workflow-model",
        json={"modelSourceId": str(ctx.source.id)},
    )
    assert response.status_code == 200
    assert response.json()["defaultWorkflowModelSourceId"] == str(ctx.source.id)
    ctx.selection.set_default_workflow_model.assert_awaited_once_with(
        str(ctx.source.id),
        updated_by=USER_ID,
    )


def test_set_default_workflow_model_maps_missing_to_404(ctx) -> None:
    ctx.selection.set_default_workflow_model.side_effect = ModelSourceNotFoundError("Model source 'missing' not found")
    response = ctx.client.put(
        "/model-gateway/selection/default-workflow-model",
        json={"modelSourceId": "missing"},
    )
    assert response.status_code == 404


def test_set_default_workflow_model_maps_mode_mismatch_to_409(ctx) -> None:
    ctx.selection.set_default_workflow_model.side_effect = ModelSourceModeMismatchError("expected 'chat'")
    response = ctx.client.put(
        "/model-gateway/selection/default-workflow-model",
        json={"modelSourceId": str(ctx.source.id)},
    )
    assert response.status_code == 409


def test_set_embedding_model_returns_202_and_triggers_reindex(ctx) -> None:
    response = ctx.client.put(
        "/model-gateway/selection/embedding-model",
        json={"modelSourceId": str(ctx.source.id)},
    )
    assert response.status_code == 202
    assert response.json()["embeddingModelSourceId"] == str(ctx.source.id)
    ctx.reindex.trigger_reindex.assert_awaited_once_with(
        str(ctx.source.id),
        updated_by=USER_ID,
    )


def test_set_embedding_model_maps_missing_to_404(ctx) -> None:
    ctx.reindex.trigger_reindex.side_effect = ModelSourceNotFoundError("Model source 'missing' not found")
    response = ctx.client.put(
        "/model-gateway/selection/embedding-model",
        json={"modelSourceId": "missing"},
    )
    assert response.status_code == 404


def test_set_embedding_model_maps_mode_mismatch_to_409(ctx) -> None:
    ctx.reindex.trigger_reindex.side_effect = ModelSourceModeMismatchError("expected 'embedding'")
    response = ctx.client.put(
        "/model-gateway/selection/embedding-model",
        json={"modelSourceId": str(ctx.source.id)},
    )
    assert response.status_code == 409


def test_set_embedding_model_maps_already_running_to_409(ctx) -> None:
    ctx.reindex.trigger_reindex.side_effect = EmbeddingReindexAlreadyRunningError("already running")
    response = ctx.client.put(
        "/model-gateway/selection/embedding-model",
        json={"modelSourceId": str(ctx.source.id)},
    )
    assert response.status_code == 409


def test_set_embedding_model_maps_smoke_test_failure_to_502(ctx) -> None:
    ctx.reindex.trigger_reindex.side_effect = EmbeddingModelSmokeTestError("bad credentials")
    response = ctx.client.put(
        "/model-gateway/selection/embedding-model",
        json={"modelSourceId": str(ctx.source.id)},
    )
    assert response.status_code == 502
    assert "bad credentials" in response.json()["detail"]["message"]


def test_scopes_config_grants_are_correct() -> None:
    """models-read for every group; models-write only for admin (scope-only authz)."""
    mappings = settings.scopes_config["group_mappings"]
    for group in (
        "jarvis-registry-admin",
        "jarvis-registry-power-user",
        "jarvis-registry-user",
        "jarvis-registry-read-only",
    ):
        assert "models-read" in mappings[group], group
    assert "models-write" in mappings["jarvis-registry-admin"]
    for group in ("jarvis-registry-power-user", "jarvis-registry-user", "jarvis-registry-read-only"):
        assert "models-write" not in mappings[group], group
