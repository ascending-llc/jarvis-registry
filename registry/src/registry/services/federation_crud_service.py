import logging
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry.utils.crypto_utils import encrypt_value
from registry_pkgs.core.crypto_utils import is_encrypted
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
    FederationLastSync,
    FederationStats,
)
from registry_pkgs.models.federation_sync_job import FederationSyncJob

logger = logging.getLogger(__name__)

_AZURE_SP_CREDENTIAL_FIELDS = ("tenantId", "clientId", "clientSecret")

# Raised when a guarded federation write finds the document no longer in its expected state (a
# concurrent sync or PUT moved it). Mapped to 409 by ``_raise_federation_value_error``.
FEDERATION_CONCURRENT_CHANGE_MSG = "Federation changed concurrently"
_STARTABLE_SYNC_STATUSES = [
    FederationSyncStatus.IDLE.value,
    FederationSyncStatus.SUCCESS.value,
    FederationSyncStatus.FAILED.value,
]
# A job's terminal parent write applies only while the parent still shows that job in flight, so a
# late write cannot overwrite a reaper repair (or another terminal write) for the same job.
_IN_FLIGHT_SYNC_STATUSES = [FederationSyncStatus.PENDING.value, FederationSyncStatus.SYNCING.value]


def _enum_value(value: Any) -> Any:
    """The stored string value for a StrEnum member, or the value unchanged if already plain."""
    return value.value if hasattr(value, "value") else value


class FederationCrudService:
    @staticmethod
    def normalize_provider_config(
        provider_type: FederationProviderType, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        # Federation creation accepts a minimal payload. Provider-specific defaults
        # are normalized here so the federation can be created before all runtime
        # sync credentials are filled in.
        raw_provider_config = dict(provider_config or {})

        if provider_type == FederationProviderType.AWS_AGENTCORE:
            if "runtimeAccess" in raw_provider_config:
                raise ValueError(
                    "providerConfig.runtimeAccess is no longer supported on federations; "
                    "runtime auth must be stored on discovered MCP server/A2A agent resources"
                )
            return AwsAgentCoreProviderConfig(**raw_provider_config).model_dump(mode="json", exclude_none=True)

        if provider_type == FederationProviderType.AZURE_AI_FOUNDRY:
            normalized = AzureAiFoundryProviderConfig(**raw_provider_config).model_dump(mode="json", exclude_none=True)
            secret = normalized.get("clientSecret")
            if secret and not is_encrypted(str(secret)):
                normalized["clientSecret"] = encrypt_value(str(secret))
            return normalized

        raise ValueError(f"Unsupported federation provider type: {provider_type}")

    @staticmethod
    def validate_provider_config(
        provider_type: FederationProviderType, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Required field check per provider type.
        """
        provider_config = FederationCrudService.normalize_provider_config(provider_type, provider_config)
        if provider_type == FederationProviderType.AWS_AGENTCORE:
            missing_fields = [
                field_name for field_name in ("region", "assumeRoleArn") if not provider_config.get(field_name)
            ]
            if missing_fields:
                missing_field_list = ", ".join(f"providerConfig.{field_name}" for field_name in missing_fields)
                raise ValueError(f"AWS AgentCore federation requires {missing_field_list}")
        elif provider_type == FederationProviderType.AZURE_AI_FOUNDRY:
            if not provider_config.get("projectEndpoint"):
                raise ValueError("Azure AI Foundry federation requires providerConfig.projectEndpoint")
            sp_present = [f for f in _AZURE_SP_CREDENTIAL_FIELDS if provider_config.get(f)]
            if sp_present and len(sp_present) < len(_AZURE_SP_CREDENTIAL_FIELDS):
                missing = [f for f in _AZURE_SP_CREDENTIAL_FIELDS if f not in sp_present]
                missing_list = ", ".join(f"providerConfig.{f}" for f in missing)
                raise ValueError(
                    f"Azure AI Foundry federation requires all service principal fields when any is set; "
                    f"missing {missing_list}"
                )
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
        session: AsyncClientSession | None = None,
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
        await federation.insert(session=session)
        return federation

    async def get_federation(
        self,
        federation_id: str,
        session: AsyncClientSession | None = None,
    ) -> Federation | None:
        try:
            object_id = PydanticObjectId(federation_id)
        except (InvalidId, TypeError, ValueError):
            return None

        federation = await Federation.get(object_id, session=session)
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

    async def _update_federation(
        self,
        federation_id: PydanticObjectId,
        set_fields: dict[str, Any],
        *,
        expected: dict[str, Any],
        updated_at: datetime | None = None,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """Targeted, guarded, non-upserting ``$set`` on one federation.

        Only ``set_fields`` are written (no whole-document overwrite), only when the document still
        matches ``expected`` (so a concurrent sync's or PUT's state is not clobbered), and never with
        ``upsert`` (so a deleted federation is not resurrected). Returns whether the guard matched;
        Mongo may report no modification when the stored values are already identical.
        """
        result = await Federation.get_pymongo_collection().update_one(
            {"_id": federation_id, **expected},
            {"$set": {**set_fields, "updatedAt": updated_at or datetime.now(UTC)}},
            session=session,
        )
        return result.matched_count == 1

    async def update_federation(
        self,
        *,
        federation: Federation,
        display_name: str,
        description: str | None,
        tags: list[str],
        provider_config: dict,
        updated_by: str | None,
        session: AsyncClientSession | None = None,
    ) -> Federation:
        normalized_config = self.validate_provider_config(federation.providerType, provider_config)

        federation.displayName = display_name
        federation.description = description
        federation.tags = tags
        federation.providerConfig = normalized_config
        federation.updatedBy = updated_by
        updated_at = datetime.now(UTC)
        updated = await self._update_federation(
            federation.id,
            {
                "displayName": display_name,
                "description": description,
                "tags": tags,
                "providerConfig": normalized_config,
                "updatedBy": updated_by,
            },
            expected={"status": FederationStatus.ACTIVE.value},
            updated_at=updated_at,
            session=session,
        )
        if not updated:
            raise ValueError(FEDERATION_CONCURRENT_CHANGE_MSG)
        federation.updatedAt = updated_at
        return federation

    async def mark_sync_pending(
        self,
        federation: Federation,
        *,
        last_sync: FederationLastSync | None = None,
        session: AsyncClientSession | None = None,
    ) -> Federation:
        federation.syncStatus = FederationStateMachine.transition_to_sync_pending(
            federation.status,
            federation.syncStatus,
        )
        federation.syncMessage = None
        if last_sync is not None:
            federation.lastSync = last_sync
        set_fields: dict[str, Any] = {"syncStatus": _enum_value(federation.syncStatus), "syncMessage": None}
        if last_sync is not None:
            set_fields["lastSync"] = last_sync.model_dump()
        updated = await self._update_federation(
            federation.id,
            set_fields,
            expected={"status": FederationStatus.ACTIVE.value, "syncStatus": {"$in": _STARTABLE_SYNC_STATUSES}},
            session=session,
        )
        if not updated:
            raise ValueError(FEDERATION_CONCURRENT_CHANGE_MSG)
        return federation

    async def mark_syncing(
        self,
        federation: Federation,
        *,
        job_id: PydanticObjectId,
        last_sync: FederationLastSync | None = None,
        session: AsyncClientSession | None = None,
    ) -> Federation:
        federation.syncStatus = FederationStateMachine.transition_to_syncing(
            federation.status,
            federation.syncStatus,
        )
        federation.syncMessage = None
        if last_sync is not None:
            federation.lastSync = last_sync
        set_fields: dict[str, Any] = {"syncStatus": _enum_value(federation.syncStatus), "syncMessage": None}
        if last_sync is not None:
            set_fields["lastSync"] = last_sync.model_dump()
        updated = await self._update_federation(
            federation.id,
            set_fields,
            expected={"status": FederationStatus.ACTIVE.value, "lastSync.jobId": job_id},
            session=session,
        )
        if not updated:
            raise ValueError(FEDERATION_CONCURRENT_CHANGE_MSG)
        return federation

    async def mark_sync_success(
        self,
        federation: Federation,
        last_sync: FederationLastSync,
        stats: FederationStats,
        *,
        job_id: PydanticObjectId,
        message: str | None = None,
        session: AsyncClientSession | None = None,
    ) -> Federation:
        federation.syncStatus = FederationStateMachine.transition_to_sync_success(federation.syncStatus)
        federation.syncMessage = message
        federation.lastSync = last_sync
        federation.stats = stats
        updated = await self._update_federation(
            federation.id,
            {
                "syncStatus": _enum_value(federation.syncStatus),
                "syncMessage": message,
                "lastSync": last_sync.model_dump(),
                "stats": stats.model_dump(),
            },
            expected={
                "status": FederationStatus.ACTIVE.value,
                "syncStatus": {"$in": _IN_FLIGHT_SYNC_STATUSES},
                "lastSync.jobId": job_id,
            },
            session=session,
        )
        if not updated:
            logger.warning("mark_sync_success skipped: federation %s no longer owned by job %s", federation.id, job_id)
        return federation

    async def mark_sync_failed(
        self,
        federation: Federation,
        message: str,
        last_sync: FederationLastSync | None = None,
        stats: FederationStats | None = None,
        *,
        job_id: PydanticObjectId | None = None,
        expected_updated_at: datetime | None = None,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """Fail the federation's sync, mutating ``federation`` in place.

        Returns whether the guarded write applied; ``False`` means the stored federation no longer
        matched (a newer job owns it, or it already left the in-flight state) and nothing was written.
        """
        federation.syncStatus = FederationStateMachine.transition_to_sync_failed(federation.syncStatus)
        federation.syncMessage = message
        if last_sync is not None:
            federation.lastSync = last_sync
        if stats is not None:
            federation.stats = stats
        set_fields: dict[str, Any] = {"syncStatus": _enum_value(federation.syncStatus), "syncMessage": message}
        if last_sync is not None:
            set_fields["lastSync"] = last_sync.model_dump()
        if stats is not None:
            set_fields["stats"] = stats.model_dump()
        # Guard on the owning job only when one is known; the stuck-federation reaper pass calls
        # this with no job to repair a federation that has no live job at all.
        expected: dict[str, Any] = {}
        if job_id is not None:
            expected = {
                "status": FederationStatus.ACTIVE.value,
                "syncStatus": {"$in": _IN_FLIGHT_SYNC_STATUSES},
                "lastSync.jobId": job_id,
            }
        if expected_updated_at is not None:
            expected.update(
                {
                    "status": FederationStatus.ACTIVE.value,
                    "syncStatus": {"$in": _IN_FLIGHT_SYNC_STATUSES},
                    "updatedAt": expected_updated_at,
                }
            )
        updated = await self._update_federation(federation.id, set_fields, expected=expected, session=session)
        if not updated:
            logger.warning("mark_sync_failed skipped: federation %s no longer owned by job %s", federation.id, job_id)
        return updated

    async def mark_delete_failed(
        self,
        federation: Federation,
        message: str,
        *,
        expected_job_id: PydanticObjectId | None,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """Restore a DELETING federation to ACTIVE with a failed sync, mutating ``federation`` in place.

        Returns whether the guarded write applied; ``False`` means the federation is gone or a
        different delete job owns it, and nothing was written.
        """
        federation.status = FederationStateMachine.transition_to_delete_failed(federation.status)
        federation.syncStatus = FederationStateMachine.transition_to_sync_failed(federation.syncStatus)
        federation.syncMessage = message
        federation.deletingJobId = None
        updated = await self._update_federation(
            federation.id,
            {
                "status": _enum_value(federation.status),
                "syncStatus": _enum_value(federation.syncStatus),
                "syncMessage": message,
                "deletingJobId": None,
            },
            expected={"status": FederationStatus.DELETING.value, "deletingJobId": expected_job_id},
            session=session,
        )
        if not updated:
            logger.info(
                "mark_delete_failed skipped: federation %s is gone or no longer owned by delete job %s",
                federation.id,
                expected_job_id,
            )
        return updated

    async def mark_deleting(
        self,
        federation: Federation,
        *,
        job_id: PydanticObjectId,
        session: AsyncClientSession | None = None,
    ) -> Federation:
        next_sync_status = FederationStateMachine.transition_to_sync_pending(
            federation.status,
            federation.syncStatus,
        )
        federation.status = FederationStateMachine.transition_to_deleting(federation.status)
        federation.syncStatus = next_sync_status
        federation.deletingJobId = job_id
        updated = await self._update_federation(
            federation.id,
            {
                "status": _enum_value(federation.status),
                "syncStatus": _enum_value(federation.syncStatus),
                "deletingJobId": job_id,
            },
            expected={"status": FederationStatus.ACTIVE.value, "syncStatus": {"$in": _STARTABLE_SYNC_STATUSES}},
            session=session,
        )
        if not updated:
            raise ValueError(FEDERATION_CONCURRENT_CHANGE_MSG)
        return federation
