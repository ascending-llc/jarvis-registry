from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry.utils.crypto_utils import encrypt_value, is_encrypted
from registry_pkgs.models.enums import (
    SkillSyncProviderType,
    SkillSyncSourceStatus,
    SkillSyncStateMachine,
    SkillSyncStatus,
)
from registry_pkgs.models.skill_sync_job import SkillSyncJob
from registry_pkgs.models.skill_sync_source import SkillSyncSource, SkillSyncSourceStats


class SkillSyncSourceCrudService:
    @staticmethod
    def _encrypt_secret(secret: str) -> str:
        return secret if is_encrypted(secret) else encrypt_value(secret)

    async def create_source(
        self,
        *,
        display_name: str,
        description: str | None,
        tags: list[str],
        owner: str,
        repo: str,
        ref: str,
        paths: list[str],
        skill_discovery_depth: int,
        github_app_client_id: str,
        github_app_client_secret: str,
        created_by: str | None,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncSource:
        source = SkillSyncSource(
            providerType=SkillSyncProviderType.GITHUB,
            displayName=display_name,
            description=description,
            tags=tags,
            owner=owner,
            repo=repo,
            ref=ref,
            paths=paths,
            skillDiscoveryDepth=skill_discovery_depth,
            githubAppClientId=github_app_client_id,
            githubAppClientSecretEncrypted=self._encrypt_secret(github_app_client_secret),
            status=SkillSyncSourceStatus.ACTIVE,
            syncStatus=SkillSyncStatus.IDLE,
            stats=SkillSyncSourceStats(),
            createdBy=created_by,
            updatedBy=created_by,
        )
        await source.insert(session=session)
        return source

    async def get_source(
        self,
        source_id: str,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncSource | None:
        try:
            object_id = PydanticObjectId(source_id)
        except (InvalidId, TypeError, ValueError):
            return None
        source = await SkillSyncSource.get(object_id, session=session)
        if source is None or source.status == SkillSyncSourceStatus.DELETED or source.deletedAt is not None:
            return None
        return source

    async def list_sources(
        self,
        *,
        sync_status: str | None = None,
        tag: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
        accessible_source_ids: list[str] | None = None,
    ) -> tuple[list[SkillSyncSource], int]:
        filters: list[dict[str, Any]] = [{"$or": [{"deletedAt": None}, {"deletedAt": {"$exists": False}}]}]
        if sync_status:
            filters.append({"syncStatus": sync_status})
        if tag:
            filters.append({"tags": tag})
        if keyword:
            filters.append({"$text": {"$search": keyword}})
        if accessible_source_ids is not None:
            if not accessible_source_ids:
                return [], 0
            filters.append({"_id": {"$in": [PydanticObjectId(value) for value in accessible_source_ids]}})
        query: dict[str, Any] = filters[0] if len(filters) == 1 else {"$and": filters}
        finder = SkillSyncSource.find(query)
        total = await finder.count()
        items = await finder.sort("-updatedAt").skip((page - 1) * page_size).limit(page_size).to_list()
        return items, total

    async def get_recent_jobs(self, source_id: PydanticObjectId, limit: int = 10) -> list[SkillSyncJob]:
        return await SkillSyncJob.find({"sourceId": source_id}).sort("-createdAt").limit(limit).to_list()

    async def update_source(
        self,
        source: SkillSyncSource,
        changes: dict[str, Any],
        *,
        updated_by: str | None,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncSource:
        if not SkillSyncStateMachine.can_update(source.status):
            raise ValueError(f"Skill sync source in status '{source.status}' cannot be updated")
        field_map = {
            "displayName": "displayName",
            "description": "description",
            "tags": "tags",
            "owner": "owner",
            "repo": "repo",
            "ref": "ref",
            "paths": "paths",
            "skillDiscoveryDepth": "skillDiscoveryDepth",
            "githubAppClientId": "githubAppClientId",
        }
        for input_name, model_name in field_map.items():
            if input_name in changes:
                setattr(source, model_name, changes[input_name])
        if "githubAppClientSecret" in changes:
            source.githubAppClientSecretEncrypted = self._encrypt_secret(changes["githubAppClientSecret"])
        source.updatedBy = updated_by
        await source.save(session=session)
        return source

    async def mark_sync_pending(
        self,
        source: SkillSyncSource,
        *,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncSource:
        source.syncStatus = SkillSyncStateMachine.transition_to_sync_pending(source.status, source.syncStatus)
        source.syncMessage = None
        await source.save(session=session)
        return source

    async def mark_sync_failed(
        self,
        source: SkillSyncSource,
        message: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncSource:
        source.syncStatus = SkillSyncStateMachine.transition_to_sync_failed(source.syncStatus)
        source.syncMessage = message
        await source.save(session=session)
        return source

    async def mark_deleting(
        self,
        source: SkillSyncSource,
        *,
        session: AsyncClientSession | None = None,
    ) -> SkillSyncSource:
        source.syncStatus = SkillSyncStateMachine.transition_to_sync_pending(source.status, source.syncStatus)
        source.status = SkillSyncStateMachine.transition_to_deleting(source.status)
        source.syncMessage = None
        await source.save(session=session)
        return source

    async def restore_after_delete_failure(self, source: SkillSyncSource, message: str) -> SkillSyncSource:
        source.status = SkillSyncSourceStatus.ACTIVE
        source.syncStatus = SkillSyncStatus.FAILED
        source.syncMessage = message
        source.updatedAt = datetime.now(UTC)
        await source.save()
        return source
