from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_gateway_selection import MODEL_GATEWAY_SELECTION_ID, ModelGatewaySelection
from registry_pkgs.models.model_source import ModelSource


class ModelGatewaySelectionService:
    async def get_selection(self) -> ModelGatewaySelection:
        """Return the singleton selection, creating it under the fixed _id if absent."""
        selection = await ModelGatewaySelection.find_one({"_id": MODEL_GATEWAY_SELECTION_ID})
        if selection is None:
            selection = ModelGatewaySelection(id=MODEL_GATEWAY_SELECTION_ID)
            try:
                await selection.insert()
            except DuplicateKeyError:
                selection = await ModelGatewaySelection.find_one({"_id": MODEL_GATEWAY_SELECTION_ID})
        return selection

    async def set_default_workflow_model(
        self,
        model_source_id: str,
        *,
        updated_by: str | None,
    ) -> ModelGatewaySelection:
        object_id = await self._require_model_source(model_source_id, ModelSourceMode.CHAT)
        selection = await self.get_selection()
        selection.defaultWorkflowModelSourceId = object_id
        selection.updatedBy = updated_by
        await selection.save()
        return selection

    async def set_embedding_model(
        self,
        model_source_id: str,
        *,
        updated_by: str | None,
    ) -> ModelGatewaySelection:
        object_id = await self._require_model_source(model_source_id, ModelSourceMode.EMBEDDING)
        selection = await self.get_selection()
        selection.embeddingModelSourceId = object_id
        selection.updatedBy = updated_by
        await selection.save()
        return selection

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
