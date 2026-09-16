from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry.utils.crypto_utils import encrypt_value
from registry_pkgs.core.crypto_utils import is_encrypted
from registry_pkgs.models.enums import ModelSourceMode, ModelSourceProviderType
from registry_pkgs.models.model_source import AwsBedrockModelConfig, AzureOpenAIModelConfig, ModelSource
from registry_pkgs.models.workflow import WorkflowDefinition
from registry_pkgs.workflows.compiler import flatten_workflow_nodes

from ..schemas.model_source_api_schemas import (
    AwsBedrockModelConfigInput,
    AzureOpenAIModelConfigInput,
    ModelSourceProviderConfigInput,
)
from .model_gateway_selection_service import ModelGatewaySelectionService

StoredProviderConfig = AwsBedrockModelConfig | AzureOpenAIModelConfig


class ModelSourceCrudService:
    def __init__(self, *, model_gateway_selection_service: ModelGatewaySelectionService) -> None:
        self._selection_service = model_gateway_selection_service

    @staticmethod
    def _encrypt_secret(secret: str) -> str:
        return secret if is_encrypted(secret) else encrypt_value(secret)

    @classmethod
    def _to_stored_config(cls, config: ModelSourceProviderConfigInput) -> StoredProviderConfig:
        if isinstance(config, AwsBedrockModelConfigInput):
            return AwsBedrockModelConfig(
                awsRegion=config.awsRegion,
                modelIdOrArn=config.modelIdOrArn,
                baseModelId=config.baseModelId,
            )
        if isinstance(config, AzureOpenAIModelConfigInput):
            return AzureOpenAIModelConfig(
                endpoint=config.endpoint,
                deploymentName=config.deploymentName,
                baseModelId=config.baseModelId,
                apiVersion=config.apiVersion,
                apiKeyEncrypted=cls._encrypt_secret(config.apiKey) if config.apiKey else None,
            )
        raise ValueError(f"Unsupported provider config: {type(config).__name__}")

    async def create_source(
        self,
        *,
        display_name: str,
        description: str | None,
        tags: list[str],
        mode: ModelSourceMode,
        provider_config: ModelSourceProviderConfigInput,
        created_by: str | None,
    ) -> ModelSource:
        source = ModelSource(
            displayName=display_name,
            description=description,
            tags=tags,
            mode=mode,
            providerConfig=self._to_stored_config(provider_config),
            createdBy=created_by,
            updatedBy=created_by,
        )
        await source.insert()
        return source

    async def get_source(self, model_source_id: str) -> ModelSource | None:
        try:
            object_id = PydanticObjectId(model_source_id)
        except (InvalidId, TypeError, ValueError):
            return None
        source = await ModelSource.get(object_id)
        if source is None or source.deletedAt is not None:
            return None
        return source

    async def list_sources(
        self,
        *,
        mode: ModelSourceMode | None = None,
        provider_type: ModelSourceProviderType | None = None,
        tag: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ModelSource], int]:
        filters: list[dict[str, Any]] = [{"$or": [{"deletedAt": None}, {"deletedAt": {"$exists": False}}]}]
        if mode:
            filters.append({"mode": mode.value})
        if provider_type:
            filters.append({"providerConfig.providerType": provider_type.value})
        if tag:
            filters.append({"tags": tag})
        if keyword:
            filters.append({"$text": {"$search": keyword}})
        query: dict[str, Any] = filters[0] if len(filters) == 1 else {"$and": filters}
        finder = ModelSource.find(query)
        total = await finder.count()
        items = await finder.sort("-updatedAt").skip((page - 1) * page_size).limit(page_size).to_list()
        return items, total

    async def update_source(
        self,
        source: ModelSource,
        changes: dict[str, Any],
        *,
        updated_by: str | None,
    ) -> ModelSource:
        if "displayName" in changes:
            source.displayName = changes["displayName"]
        if "description" in changes:
            source.description = changes["description"]
        if "tags" in changes:
            source.tags = changes["tags"]
        if "mode" in changes:
            # Guard the gateway-selection invariant: a source referenced as a default slot
            # must keep the mode that slot requires. Symmetric with the delete guard.
            if changes["mode"] != source.mode and await self.is_in_use(str(source.id)):
                raise ValueError("Cannot change the mode of a model source that is in use as a default selection")
            source.mode = changes["mode"]
        provider_config = changes.get("providerConfig")
        if provider_config is not None:
            new_config = self._to_stored_config(provider_config)
            source.providerConfig = self._preserve_existing_secret(new_config, source.providerConfig)
        source.updatedBy = updated_by
        await source.save()
        return source

    @staticmethod
    def _preserve_existing_secret(
        new_config: StoredProviderConfig,
        old_config: StoredProviderConfig,
    ) -> StoredProviderConfig:
        """Carry over the stored Azure key when an update omits a new one."""
        if (
            isinstance(new_config, AzureOpenAIModelConfig)
            and new_config.apiKeyEncrypted is None
            and isinstance(old_config, AzureOpenAIModelConfig)
            and old_config.apiKeyEncrypted
        ):
            new_config.apiKeyEncrypted = old_config.apiKeyEncrypted
        return new_config

    async def is_in_use(self, model_source_id: str) -> bool:
        """Return True when a ModelSource is referenced and must not be deleted."""
        try:
            object_id = PydanticObjectId(model_source_id)
        except (InvalidId, TypeError, ValueError):
            return False
        selection = await self._selection_service.get_selection()
        if object_id in (selection.defaultWorkflowModelSourceId, selection.embeddingModelSourceId):
            return True
        return await self._referenced_by_workflow(object_id)

    @staticmethod
    async def _referenced_by_workflow(model_source_id: PydanticObjectId) -> bool:
        """Scan workflow node trees for a ModelSource reference.

        Workflow deletion is low-frequency and the recursive tree cannot be queried reliably as a
        single indexed Mongo path, so this intentionally scans definitions until the first match.
        """
        async for definition in WorkflowDefinition.find({}):
            for node in flatten_workflow_nodes(definition.nodes):
                if node.model_source_id is None:
                    continue
                try:
                    if PydanticObjectId(node.model_source_id) == model_source_id:
                        return True
                except (InvalidId, TypeError, ValueError):
                    continue
        return False

    @staticmethod
    async def soft_delete(source: ModelSource) -> ModelSource:
        source.deletedAt = datetime.now(UTC)
        await source.save()
        return source
