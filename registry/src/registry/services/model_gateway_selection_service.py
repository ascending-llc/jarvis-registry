from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry_pkgs.database.model_gateway_selection_repository import (
    get_model_gateway_selection,
    set_model_gateway_selection,
)
from registry_pkgs.models.enums import ModelSourceMode
from registry_pkgs.models.model_gateway_selection import ModelGatewaySelection
from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.skill_scan.plan import SKILL_SCAN_PROVIDER_TYPES

# Rejection for a skill-scan source outside SKILL_SCAN_PROVIDER_TYPES (AS-1980 D7).
_SKILL_SCAN_PROVIDER_REJECTED = (
    "Model source '{model_source_id}' uses provider '{provider}', which cannot scan skills: skill content must "
    "not leave the client's cloud account, so only AWS Bedrock and Azure OpenAI can scan skills."
)


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

    async def set_skill_scan_model(
        self,
        model_source_id: str | None,
        *,
        updated_by: str | None,
    ) -> ModelGatewaySelection:
        """Set (or clear, with ``None``) the model used for skill security scanning.

        A non-null id must point at a live CHAT ModelSource on a ``SKILL_SCAN_PROVIDER_TYPES`` provider
        (``ModelSourceModeMismatchError`` otherwise, which the route maps to 409); ``None`` clears the
        slot so scanning falls back to ``defaultWorkflowModelSourceId``.
        """
        source_id = None
        if model_source_id is not None:
            source = await self._require_model_source(model_source_id, ModelSourceMode.CHAT)
            provider = source.providerConfig.providerType
            if provider not in SKILL_SCAN_PROVIDER_TYPES:
                raise ModelSourceModeMismatchError(
                    _SKILL_SCAN_PROVIDER_REJECTED.format(model_source_id=model_source_id, provider=provider)
                )
            source_id = source.id
        return await set_model_gateway_selection(
            "skillScanModelSourceId",
            source_id,
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
