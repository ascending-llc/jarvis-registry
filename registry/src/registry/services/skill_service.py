"""Business logic for Skill CRUD, sync-down, and ACL enforcement."""

# ruff: noqa: UP045 -- Repository guidance requires explicit Optional[T] annotations.

import base64
import logging
from datetime import UTC, datetime
from typing import Optional

from beanie import PydanticObjectId
from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile
from registry_pkgs.models import PrincipalType, SkillSource
from registry_pkgs.models.enums import RoleBits
from registry_pkgs.models.extended_access_role import RegistryResourceType

from ..schemas.acl_schema import ResourcePermissions
from ..schemas.skill_api_schemas import (
    SkillCreateRequest,
    SkillFileContentResponse,
    SkillFileMetadataResponse,
    SkillFileResponse,
    SkillUpdateRequest,
)
from .access_control_service import ACLService
from .user_service import UserService

logger = logging.getLogger(__name__)

_REGISTRY_FILE_SOURCE = "registry-inline"
_UNAVAILABLE_FILE_REASON = "File content is not available in Registry because it was created in Jarvis Chat."
_FRONTMATTER_FIELDS = {"name", "description", "alwaysApply", "userInvocable", "disableModelInvocation", "allowedTools"}


def _require_user_id(user_id: Optional[str]) -> PydanticObjectId:
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user ID is required")
    try:
        return PydanticObjectId(user_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user ID is invalid") from e


def _file_metadata(skill_file: SkillFile) -> SkillFileMetadataResponse:
    return SkillFileMetadataResponse(
        id=str(skill_file.id),
        relativePath=skill_file.relativePath,
        mimeType=skill_file.mimeType,
        bytes=skill_file.bytes,
        isBinary=skill_file.isBinary,
        isExecutable=skill_file.isExecutable,
        source=skill_file.source,
    )


def _registry_file_text(skill_file: SkillFile) -> Optional[str]:
    if skill_file.body is None or skill_file.isBinary:
        return None
    try:
        return skill_file.body.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _sync_file_response(skill_file: SkillFile) -> SkillFileResponse:
    if skill_file.source != _REGISTRY_FILE_SOURCE:
        return SkillFileResponse(
            relativePath=skill_file.relativePath,
            mimeType=skill_file.mimeType,
            bytes=skill_file.bytes,
            isBinary=skill_file.isBinary,
            isExecutable=skill_file.isExecutable,
            source=skill_file.source,
            available=False,
            unavailableReason=_UNAVAILABLE_FILE_REASON,
        )

    return SkillFileResponse(
        relativePath=skill_file.relativePath,
        content=_registry_file_text(skill_file),
        mimeType=skill_file.mimeType,
        bytes=skill_file.bytes,
        isBinary=skill_file.isBinary,
        isExecutable=skill_file.isExecutable,
        source=skill_file.source,
    )


class SkillService:
    """App-scoped service for Skill persistence and permissions."""

    def __init__(self, acl_service: ACLService, user_service: UserService):
        self.acl_service = acl_service
        self.user_service = user_service

    async def list_skills(
        self,
        user_id: Optional[str],
        *,
        enabled: Optional[bool] = None,
        file_count: Optional[int] = None,
    ) -> list[tuple[Skill, ResourcePermissions]]:
        object_user_id = _require_user_id(user_id)
        accessible_ids = await self.acl_service.get_accessible_resource_ids(
            user_id=object_user_id,
            resource_type=RegistryResourceType.SKILL.value,
        )
        if not accessible_ids:
            return []

        query: dict = {
            "_id": {"$in": [PydanticObjectId(resource_id) for resource_id in accessible_ids]},
            "deletedAt": None,
        }
        if enabled is not None:
            query["enabled"] = enabled
        if file_count is not None:
            query["fileCount"] = file_count

        skills = await Skill.find(query).sort("+updatedAt").to_list()
        resource_ids = [skill.id for skill in skills if skill.id is not None]
        permissions = await self.acl_service.get_user_permissions_for_resources(
            user_id=object_user_id,
            resource_type=RegistryResourceType.SKILL.value,
            resource_ids=resource_ids,
        )
        logger.debug("list_skills: returned %d ACL-filtered skills", len(skills))
        return [(skill, permissions[skill.id]) for skill in skills if skill.id is not None]

    async def get_skill(
        self,
        skill_id: PydanticObjectId,
        user_id: Optional[str],
    ) -> tuple[Skill, list[SkillFile], ResourcePermissions]:
        skill = await self._get_existing_skill(skill_id)
        permissions = await self.acl_service.check_user_permission(
            user_id=_require_user_id(user_id),
            resource_type=RegistryResourceType.SKILL.value,
            resource_id=skill_id,
            required_permission="VIEW",
        )
        files = await self._list_skill_files(skill_id)
        return skill, files, permissions

    async def get_skill_with_files(
        self,
        skill_id: PydanticObjectId,
        user_id: Optional[str],
    ) -> tuple[Skill, list[SkillFileResponse]]:
        skill, files, _permissions = await self.get_skill(skill_id, user_id)
        return skill, [_sync_file_response(skill_file) for skill_file in files]

    async def get_skill_file_content(
        self,
        skill_id: PydanticObjectId,
        relative_path: str,
        user_id: Optional[str],
    ) -> SkillFileContentResponse:
        await self._get_existing_skill(skill_id)
        await self.acl_service.check_user_permission(
            user_id=_require_user_id(user_id),
            resource_type=RegistryResourceType.SKILL.value,
            resource_id=skill_id,
            required_permission="VIEW",
        )
        skill_file = await SkillFile.find_one({"skillId": skill_id, "relativePath": relative_path})
        if skill_file is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill file not found")
        if skill_file.source != _REGISTRY_FILE_SOURCE:
            return SkillFileContentResponse(
                relativePath=skill_file.relativePath,
                mimeType=skill_file.mimeType,
                isBinary=skill_file.isBinary,
                available=False,
                unavailableReason=_UNAVAILABLE_FILE_REASON,
            )

        text_content = _registry_file_text(skill_file)
        binary_body = None
        if text_content is None and skill_file.body is not None:
            binary_body = base64.b64encode(skill_file.body).decode("ascii")
        return SkillFileContentResponse(
            relativePath=skill_file.relativePath,
            content=text_content,
            body=binary_body,
            mimeType=skill_file.mimeType,
            isBinary=skill_file.isBinary,
        )

    async def create_skill(
        self,
        data: SkillCreateRequest,
        user_id: Optional[str],
        author_name: Optional[str],
    ) -> tuple[Skill, ResourcePermissions]:
        object_user_id = _require_user_id(user_id)
        user = await self.user_service.get_user_by_user_id(str(object_user_id))
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user was not found")
        resolved_author_name = str(user.name or user.username or author_name or "").strip()
        if not resolved_author_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill author name is required")

        duplicate_query = {
            "name": data.name,
            "author": object_user_id,
            "deletedAt": None,
        }
        if await Skill.find_one(duplicate_query):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A skill with this name already exists")

        now = datetime.now(UTC)
        skill = Skill(
            **data.model_dump(),
            author=object_user_id,
            authorName=resolved_author_name,
            frontmatter=self._frontmatter_from_create(data),
            path=data.name,
            source=SkillSource.INLINE,
            enabled=True,
            createdByRegistry=True,
            fileCount=0,
            version=1,
            createdAt=now,
            updatedAt=now,
        )
        try:
            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    await skill.insert(session=mongo_session)
                    await self.acl_service.grant_permission(
                        principal_type=PrincipalType.USER,
                        principal_id=object_user_id,
                        resource_type=RegistryResourceType.SKILL.value,
                        resource_id=skill.id,
                        perm_bits=RoleBits.OWNER,
                        session=mongo_session,
                    )
        except DuplicateKeyError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="A skill with this name already exists"
            ) from e

        return skill, ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)

    async def update_skill(
        self,
        skill_id: PydanticObjectId,
        data: SkillUpdateRequest,
        user_id: Optional[str],
    ) -> tuple[Skill, list[SkillFile], ResourcePermissions]:
        object_user_id = _require_user_id(user_id)
        # _get_existing_skill loads the document via find_one, which seeds Beanie's
        # state management snapshot.  The subsequent save() therefore emits an
        # incremental $set instead of a full-document replace, preserving any
        # Chat-only fields that are not modelled on ExtendedSkill.
        skill = await self._get_existing_skill(skill_id)
        updates = data.model_dump(exclude_unset=True)
        _nullable_fields = {"displayTitle", "allowedTools"}
        invalid_nulls = [k for k, v in updates.items() if v is None and k not in _nullable_fields]
        if invalid_nulls:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Fields cannot be null: {', '.join(sorted(invalid_nulls))}",
            )
        if not updates:
            permissions = await self.acl_service.check_user_permission(
                user_id=object_user_id,
                resource_type=RegistryResourceType.SKILL.value,
                resource_id=skill_id,
                required_permission="EDIT",
            )
            return skill, await self._list_skill_files(skill_id), permissions
        try:
            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    permissions = await self.acl_service.check_user_permission(
                        user_id=object_user_id,
                        resource_type=RegistryResourceType.SKILL.value,
                        resource_id=skill_id,
                        required_permission="EDIT",
                        session=mongo_session,
                    )
                    if "name" in updates and updates["name"] != skill.name:
                        duplicate = await Skill.find_one(
                            {
                                "_id": {"$ne": skill_id},
                                "name": updates["name"],
                                "author": skill.author,
                                "deletedAt": None,
                            },
                            session=mongo_session,
                        )
                        if duplicate:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail="A skill with this name already exists",
                            )
                    for field_name, value in updates.items():
                        setattr(skill, field_name, value)
                    if "name" in updates:
                        skill.path = updates["name"]
                    self._update_frontmatter(skill, updates)
                    skill.version += 1
                    skill.updatedAt = datetime.now(UTC)
                    await skill.save(session=mongo_session)
        except DuplicateKeyError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="A skill with this name already exists"
            ) from e

        return skill, await self._list_skill_files(skill_id), permissions

    async def delete_skill(self, skill_id: PydanticObjectId, user_id: Optional[str]) -> None:
        skill = await self._get_existing_skill(skill_id)
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                await self.acl_service.check_user_permission(
                    user_id=_require_user_id(user_id),
                    resource_type=RegistryResourceType.SKILL.value,
                    resource_id=skill_id,
                    required_permission="DELETE",
                    session=mongo_session,
                )
                if not skill.createdByRegistry:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Only skills created in Registry can be deleted from Registry",
                    )
                await SkillFile.find({"skillId": skill_id}).delete(session=mongo_session)
                await skill.delete(session=mongo_session)
                await self.acl_service.delete_acl_entries_for_resource(
                    resource_type=RegistryResourceType.SKILL.value,
                    resource_id=skill_id,
                    session=mongo_session,
                )

    async def toggle_skill(
        self,
        skill_id: PydanticObjectId,
        enabled: bool,
        user_id: Optional[str],
    ) -> Skill:
        skill = await self._get_existing_skill(skill_id)
        await self.acl_service.check_user_permission(
            user_id=_require_user_id(user_id),
            resource_type=RegistryResourceType.SKILL.value,
            resource_id=skill_id,
            required_permission="EDIT",
        )
        skill.enabled = enabled
        skill.updatedAt = datetime.now(UTC)
        await skill.save()
        return skill

    @staticmethod
    async def _get_existing_skill(skill_id: PydanticObjectId) -> Skill:
        skill = await Skill.find_one({"_id": skill_id, "deletedAt": None})
        if skill is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        return skill

    @staticmethod
    async def _list_skill_files(skill_id: PydanticObjectId) -> list[SkillFile]:
        files = await SkillFile.find({"skillId": skill_id}).to_list()
        return sorted(files, key=lambda skill_file: skill_file.relativePath)

    @staticmethod
    def _frontmatter_from_create(data: SkillCreateRequest) -> dict:
        dumped = data.model_dump()
        return {k: dumped[k] for k in _FRONTMATTER_FIELDS if k in dumped}

    @staticmethod
    def _update_frontmatter(skill: Skill, updates: dict) -> None:
        frontmatter = dict(skill.frontmatter)
        for field_name in _FRONTMATTER_FIELDS.intersection(updates):
            frontmatter[field_name] = updates[field_name]
        skill.frontmatter = frontmatter


def skill_file_metadata(skill_file: SkillFile) -> SkillFileMetadataResponse:
    """Convert an ODM file to API metadata without exposing stored content."""
    return _file_metadata(skill_file)
