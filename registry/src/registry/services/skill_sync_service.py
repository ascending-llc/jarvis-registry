from __future__ import annotations

from beanie import PydanticObjectId

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import PrincipalType
from registry_pkgs.models.enums import RoleBits
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.skill_sync_source import SkillSyncSource

from .access_control_service import ACLService
from .skill_sync_source_crud_service import SkillSyncSourceCrudService

SKILL_SYNC_EXECUTION_UNAVAILABLE_DETAIL = "Skill sync execution is not available yet"


class SkillSyncService:
    def __init__(self, source_crud_service: SkillSyncSourceCrudService) -> None:
        self._source_crud_service = source_crud_service

    async def create_source_with_owner_acl(
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
        created_by: str,
        principal_id: PydanticObjectId,
        acl_service: ACLService,
    ) -> SkillSyncSource:
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                source = await self._source_crud_service.create_source(
                    display_name=display_name,
                    description=description,
                    tags=tags,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    paths=paths,
                    skill_discovery_depth=skill_discovery_depth,
                    github_app_client_id=github_app_client_id,
                    github_app_client_secret=github_app_client_secret,
                    created_by=created_by,
                    session=mongo_session,
                )
                await acl_service.grant_permission(
                    principal_type=PrincipalType.USER,
                    principal_id=principal_id,
                    resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
                    resource_id=source.id,
                    perm_bits=RoleBits.OWNER,
                    session=mongo_session,
                )
        return source
