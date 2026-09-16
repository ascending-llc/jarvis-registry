from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry_pkgs.database.model_gateway_selection_repository import (
    get_model_gateway_selection,
    set_model_gateway_selection,
)
from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_gateway_selection import ModelGatewaySelection
from registry_pkgs.models.model_source import ModelSource


class ModelGatewaySelectionService:
    async def get_selection(self) -> ModelGatewaySelection:
        """Return the singleton selection, creating it under the fixed _id if absent."""
        selection = await get_model_gateway_selection(create_if_missing=True)
        if selection is None:
            raise RuntimeError("Failed to create model gateway selection")
        return selection

    async def set_default_workflow_model(
        self,
        model_source_id: str,
        *,
        updated_by: str | None,
    ) -> ModelGatewaySelection:
        object_id = await self._require_model_source(model_source_id, ModelSourceMode.CHAT)
        return await set_model_gateway_selection(
            "defaultWorkflowModelSourceId",
            object_id,
            updated_by=updated_by,
        )

    async def set_embedding_model(
        self,
        model_source_id: str,
        *,
        updated_by: str | None,
    ) -> ModelGatewaySelection:
        object_id = await self._require_model_source(model_source_id, ModelSourceMode.EMBEDDING)
        return await set_model_gateway_selection(
            "embeddingModelSourceId",
            object_id,
            updated_by=updated_by,
        )

    @staticmethod
    async def _require_model_source(model_source_id: str, expected_mode: ModelSourceMode) -> PydanticObjectId:
        try:
            object_id = PydanticObjectId(model_source_id)
        except (InvalidId, TypeError, ValueError) as exc:
            raise ValueError(f"Model source '{model_source_id}' not found") from exc
        source = await ModelSource.get(object_id)
        if source is None or source.deletedAt is not None:
            raise ValueError(f"Model source '{model_source_id}' not found")
        if source.mode != expected_mode:
            raise ValueError(f"Model source '{model_source_id}' has mode '{source.mode}', expected '{expected_mode}'")
        return object_id
