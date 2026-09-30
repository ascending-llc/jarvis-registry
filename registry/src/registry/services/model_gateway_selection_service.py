from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry_pkgs.database.model_gateway_selection_repository import (
    get_model_gateway_selection,
    set_model_gateway_selection,
)
from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_gateway_selection import ModelGatewaySelection
from registry_pkgs.models.model_source import ModelSource


class ModelSourceNotFoundError(ValueError):
    """Raised when a referenced ModelSource is missing, soft-deleted, or has an invalid id."""


class ModelSourceModeMismatchError(ValueError):
    """Raised when a referenced ModelSource exists but has the wrong mode for the target slot."""


class ModelGatewaySelectionService:
    async def get_selection(self) -> ModelGatewaySelection:
        """Return the singleton selection, creating it under the fixed _id if absent."""
        selection = await get_model_gateway_selection(create_if_missing=True)
        if selection is None:
            raise RuntimeError("Failed to create model gateway selection")
        return selection

    async def get_selection_or_none(self) -> ModelGatewaySelection | None:
        """Read the singleton without creating it — for read-only callers such as delete guards."""
        return await get_model_gateway_selection(create_if_missing=False)

    async def set_default_workflow_model(
        self,
        model_source_id: str,
        *,
        updated_by: str | None,
    ) -> ModelGatewaySelection:
        source = await self._require_model_source(model_source_id, ModelSourceMode.CHAT)
        return await set_model_gateway_selection(
            "defaultWorkflowModelSourceId",
            source.id,
            updated_by=updated_by,
        )

    async def resolve_embedding_model_source(self, model_source_id: str) -> ModelSource:
        """Validate the id points at a live EMBEDDING ModelSource and return it.

        Lets the reindex trigger smoke-test the model before persisting the selection, reusing the
        same not-found/mode-mismatch guards ``set_default_workflow_model`` applies.
        """
        return await self._require_model_source(model_source_id, ModelSourceMode.EMBEDDING)

    @staticmethod
    async def _require_model_source(model_source_id: str, expected_mode: ModelSourceMode) -> ModelSource:
        try:
            object_id = PydanticObjectId(model_source_id)
        except (InvalidId, TypeError, ValueError) as exc:
            raise ModelSourceNotFoundError(f"Model source '{model_source_id}' not found") from exc
        source = await ModelSource.get(object_id)
        if source is None or source.deletedAt is not None:
            raise ModelSourceNotFoundError(f"Model source '{model_source_id}' not found")
        if source.mode != expected_mode:
            raise ModelSourceModeMismatchError(
                f"Model source '{model_source_id}' has mode '{source.mode}', expected '{expected_mode}'"
            )
        return source
