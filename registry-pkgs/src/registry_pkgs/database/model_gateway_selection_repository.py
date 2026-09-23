from datetime import UTC, datetime
from typing import Literal

from beanie import PydanticObjectId
from pymongo import ReturnDocument

from registry_pkgs.models.model_gateway_selection import MODEL_GATEWAY_SELECTION_ID, ModelGatewaySelection

SelectionField = Literal["defaultWorkflowModelSourceId", "embeddingModelSourceId"]


async def get_model_gateway_selection(*, create_if_missing: bool) -> ModelGatewaySelection | None:
    """Read the singleton by its fixed id, optionally creating it atomically."""
    collection = ModelGatewaySelection.get_pymongo_collection()
    if create_if_missing:
        document = await collection.find_one_and_update(
            {"_id": MODEL_GATEWAY_SELECTION_ID},
            {"$setOnInsert": {"updatedAt": datetime.now(UTC)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    else:
        document = await collection.find_one({"_id": MODEL_GATEWAY_SELECTION_ID})
    return ModelGatewaySelection.model_validate(document) if document is not None else None


async def set_model_gateway_selection(
    field: SelectionField,
    model_source_id: PydanticObjectId,
    *,
    updated_by: str | None,
) -> ModelGatewaySelection:
    """Atomically update one selection slot without overwriting the other slot."""
    document = await ModelGatewaySelection.get_pymongo_collection().find_one_and_update(
        {"_id": MODEL_GATEWAY_SELECTION_ID},
        {
            "$set": {
                field: model_source_id,
                "updatedBy": updated_by,
                "updatedAt": datetime.now(UTC),
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if document is None:  # defensive: ReturnDocument.AFTER + upsert should always return a document
        raise RuntimeError("Failed to update model gateway selection")
    return ModelGatewaySelection.model_validate(document)
