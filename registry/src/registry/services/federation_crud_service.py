import logging
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId

from registry_pkgs.database.decorators import get_current_session
from registry_pkgs.models.enums import (
    FederationProviderType,
    FederationStateMachine,
    FederationStatus,
    FederationSyncStatus,
)
from registry_pkgs.models.federation import (
    AwsAgentCoreProviderConfig,
    AzureAiFoundryProviderConfig,
    Federation,
    FederationStats,
)
from registry_pkgs.models.federation_sync_job import FederationSyncJob

logger = logging.getLogger(__name__)


class FederationCrudService:
    @staticmethod
    def _get_current_session_or_none():
        try:
            return get_current_session()
        except RuntimeError:
            return None

    @staticmethod
    def normalize_provider_config(
        provider_type: FederationProviderType, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        # Federation creation accepts a minimal payload. Provider-specific defaults
        # are normalized here so the federation can be created before all runtime
        # sync credentials are filled in.
        provider_config = dict(provider_config or {})

        if provider_type == FederationProviderType.AWS_AGENTCORE:
            return AwsAgentCoreProviderConfig(**provider_config).model_dump(mode="json", exclude_none=True)

        if provider_type == FederationProviderType.AZURE_AI_FOUNDRY:
            return AzureAiFoundryProviderConfig(**provider_config).model_dump(mode="json", exclude_none=True)

        raise ValueError(f"Unsupported federation provider type: {provider_type}")

    @staticmethod
    def validate_provider_config(
        provider_type: FederationProviderType, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """
        region、assumeRoleArn fields must have values
        """
        provider_config = FederationCrudService.normalize_provider_config(provider_type, provider_config)
        if provider_type == FederationProviderType.AWS_AGENTCORE:
            missing_fields = [
                field_name for field_name in ("region", "assumeRoleArn") if not provider_config.get(field_name)
            ]
            if missing_fields:
                missing_field_list = ", ".join(f"providerConfig.{field_name}" for field_name in missing_fields)
                raise ValueError(f"AWS AgentCore federation requires {missing_field_list}")
        if provider_type == FederationProviderType.AZURE_AI_FOUNDRY and not provider_config.get("projectEndpoint"):
            raise ValueError("Azure AI Foundry federation requires providerConfig.projectEndpoint")
        return provider_config

    async def create_federation(
        self,
        *,
        provider_type,
        display_name: str,
        description: str | None,
        tags: list[str],
        provider_config: dict,
        created_by: str | None,
    ) -> Federation:
        # Creation only persists the federation definition. Provider sync is
        # always triggered explicitly through the dedicated sync API.
        normalized_config = self.normalize_provider_config(provider_type, provider_config)
        federation = Federation(
            providerType=provider_type,
            displayName=display_name,
            description=description,
            tags=tags,
            providerConfig=normalized_config,
            status=FederationStatus.ACTIVE,
            syncStatus=FederationSyncStatus.IDLE,
            createdBy=created_by,
            updatedBy=created_by,
            stats=FederationStats(),
        )
        await federation.insert(session=self._get_current_session_or_none())
        return federation

    async def get_federation(self, federation_id: str) -> Federation | None:
        try:
            object_id = PydanticObjectId(federation_id)
        except (InvalidId, TypeError, ValueError):
            return None

        federation = await Federation.get(object_id)
        if not federation or federation.status == FederationStatus.DELETED or federation.deletedAt is not None:
            return None
        return federation

    async def list_federations(
        self,
        *,
        provider_type: str | None = None,
        sync_status: str | None = None,
        tag: str | None = None,
        tags: list[str] | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
        accessible_federation_ids: list[str] | None = None,
    ) -> tuple[list[Federation], int]:
        query: dict = {
            "$or": [
                {"deletedAt": None},
                {"deletedAt": {"$exists": False}},
            ]
        }

        and_filters: list[dict] = []

        if provider_type:
            and_filters.append({"providerType": provider_type})

        if sync_status:
            and_filters.append({"syncStatus": sync_status})

        if tag:
            and_filters.append({"tags": tag})

        if tags:
            and_filters.append({"tags": {"$all": tags}})

        if keyword:
            and_filters.append({"$text": {"$search": keyword}})

        if accessible_federation_ids is not None:
            if not accessible_federation_ids:
                return [], 0
            and_filters.append(
                {"_id": {"$in": [PydanticObjectId(resource_id) for resource_id in accessible_federation_ids]}}
            )

        if and_filters:
            query = {"$and": [query, *and_filters]}

        skip = (page - 1) * page_size
        items = await Federation.find(query).sort("-updatedAt").skip(skip).limit(page_size).to_list()
        total = await Federation.find(query).count()
        return items, total

    async def get_recent_jobs(
        self,
        federation_id: PydanticObjectId,
        limit: int = 10,
    ) -> list[FederationSyncJob]:
        return await FederationSyncJob.find({"federationId": federation_id}).sort("-createdAt").limit(limit).to_list()

    async def update_federation(
        self,
        *,
        federation: Federation,
        display_name: str,
        description: str | None,
        tags: list[str],
        provider_config: dict,
        version: int,
        updated_by: str | None,
    ) -> Federation:
        if federation.version != version:
            raise ValueError("Federation version conflict")

        normalized_config = self.validate_provider_config(federation.providerType, provider_config)

        federation.displayName = display_name
        federation.description = description
        federation.tags = tags
        federation.providerConfig = normalized_config
        federation.updatedBy = updated_by
        federation.version += 1
        await federation.save(session=self._get_current_session_or_none())
        return federation

    async def mark_sync_pending(self, federation: Federation) -> Federation:
        federation.syncStatus = FederationStateMachine.transition_to_sync_pending(
            federation.status,
            federation.syncStatus,
        )
        federation.syncMessage = None
        await federation.save(session=self._get_current_session_or_none())
        return federation

    async def mark_syncing(self, federation: Federation) -> Federation:
        federation.syncStatus = FederationStateMachine.transition_to_syncing(
            federation.status,
            federation.syncStatus,
        )
        federation.syncMessage = None
        await federation.save(session=self._get_current_session_or_none())
        return federation

    async def mark_sync_success(self, federation: Federation, last_sync, stats: FederationStats) -> Federation:
        federation.syncStatus = FederationStateMachine.transition_to_sync_success(federation.syncStatus)
        federation.syncMessage = None
        federation.lastSync = last_sync
        federation.stats = stats
        await federation.save(session=self._get_current_session_or_none())
        return federation

    async def mark_sync_failed(
        self,
        federation: Federation,
        message: str,
        last_sync=None,
        stats: FederationStats | None = None,
    ) -> Federation:
        federation.syncStatus = FederationStateMachine.transition_to_sync_failed(federation.syncStatus)
        federation.syncMessage = message
        if last_sync is not None:
            federation.lastSync = last_sync
        if stats is not None:
            federation.stats = stats
        await federation.save(session=self._get_current_session_or_none())
        return federation

    async def mark_delete_failed(self, federation: Federation, message: str) -> Federation:
        federation.status = FederationStateMachine.transition_to_delete_failed(federation.status)
        federation.syncStatus = FederationStateMachine.transition_to_sync_failed(federation.syncStatus)
        federation.syncMessage = message
        await federation.save(session=self._get_current_session_or_none())
        return federation

    async def mark_deleting(self, federation: Federation) -> Federation:
        next_sync_status = FederationStateMachine.transition_to_sync_pending(
            federation.status,
            federation.syncStatus,
        )
        federation.status = FederationStateMachine.transition_to_deleting(federation.status)
        federation.syncStatus = next_sync_status
        await federation.save(session=self._get_current_session_or_none())
        return federation

    async def mark_deleted(self, federation: Federation) -> Federation:
        federation.status = FederationStateMachine.transition_to_deleted(federation.status)
        federation.deletedAt = datetime.now(UTC)
        federation.syncStatus = FederationStateMachine.transition_to_sync_success(federation.syncStatus)
        await federation.save(session=self._get_current_session_or_none())
        return federation
