from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId
from pymongo import ReturnDocument

from registry_pkgs.database import model_gateway_selection_repository as repository


@pytest.mark.asyncio
async def test_get_selection_reads_fixed_singleton_id(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = AsyncMock()
    collection.find_one.return_value = None
    monkeypatch.setattr(repository.ModelGatewaySelection, "get_pymongo_collection", lambda *_args: collection)

    result = await repository.get_model_gateway_selection(create_if_missing=False)

    assert result is None
    collection.find_one.assert_awaited_once_with({"_id": repository.MODEL_GATEWAY_SELECTION_ID})


@pytest.mark.asyncio
async def test_get_selection_creates_singleton_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {"_id": repository.MODEL_GATEWAY_SELECTION_ID, "updatedAt": datetime.now().astimezone()}
    collection = AsyncMock()
    collection.find_one_and_update.return_value = document
    monkeypatch.setattr(repository.ModelGatewaySelection, "get_pymongo_collection", lambda *_args: collection)

    result = await repository.get_model_gateway_selection(create_if_missing=True)

    assert result is not None and result.id == repository.MODEL_GATEWAY_SELECTION_ID
    args, kwargs = collection.find_one_and_update.await_args
    assert args[0] == {"_id": repository.MODEL_GATEWAY_SELECTION_ID}
    assert "$setOnInsert" in args[1]
    assert kwargs == {"upsert": True, "return_document": ReturnDocument.AFTER}


@pytest.mark.asyncio
async def test_set_selection_updates_only_requested_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    source_id = PydanticObjectId()
    document = {
        "_id": repository.MODEL_GATEWAY_SELECTION_ID,
        "defaultWorkflowModelSourceId": source_id,
        "embeddingModelSourceId": PydanticObjectId(),
        "updatedBy": "admin",
        "updatedAt": datetime.now().astimezone(),
    }
    collection = AsyncMock()
    collection.find_one_and_update.return_value = document
    monkeypatch.setattr(repository.ModelGatewaySelection, "get_pymongo_collection", lambda *_args: collection)

    result = await repository.set_model_gateway_selection(
        "defaultWorkflowModelSourceId",
        source_id,
        updated_by="admin",
    )

    assert result.defaultWorkflowModelSourceId == source_id
    update = collection.find_one_and_update.await_args.args[1]
    assert update["$set"]["defaultWorkflowModelSourceId"] == source_id
    assert "embeddingModelSourceId" not in update["$set"]


@pytest.mark.asyncio
async def test_commit_embedding_generation_cas_matches_expected_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    source_id = PydanticObjectId()
    document = {
        "_id": repository.MODEL_GATEWAY_SELECTION_ID,
        "embeddingModelSourceId": source_id,
        "embeddingCollectionGeneration": "genNEW",
        "updatedBy": "admin",
        "updatedAt": datetime.now().astimezone(),
    }
    collection = AsyncMock()
    collection.find_one_and_update.return_value = document
    monkeypatch.setattr(repository.ModelGatewaySelection, "get_pymongo_collection", lambda *_args: collection)

    result = await repository.commit_embedding_generation(
        expected_generation="genOLD", model_source_id=source_id, generation="genNEW", updated_by="admin"
    )

    assert result is not None and result.embeddingCollectionGeneration == "genNEW"
    # Compare-and-set: the filter pins the expected current generation; upsert must be off.
    args, kwargs = collection.find_one_and_update.await_args
    assert args[0] == {"_id": repository.MODEL_GATEWAY_SELECTION_ID, "embeddingCollectionGeneration": "genOLD"}
    assert args[1]["$set"]["embeddingModelSourceId"] == source_id
    assert args[1]["$set"]["embeddingCollectionGeneration"] == "genNEW"
    assert kwargs["upsert"] is False


@pytest.mark.asyncio
async def test_commit_embedding_generation_returns_none_on_lost_race(monkeypatch: pytest.MonkeyPatch) -> None:
    # Another reindex already moved the generation, so the CAS filter no longer matches → no document.
    collection = AsyncMock()
    collection.find_one_and_update.return_value = None
    monkeypatch.setattr(repository.ModelGatewaySelection, "get_pymongo_collection", lambda *_args: collection)

    result = await repository.commit_embedding_generation(
        expected_generation="genOLD", model_source_id=PydanticObjectId(), generation="genNEW", updated_by=None
    )

    assert result is None
