"""Business logic for Skill CRUD, sync-down, and ACL enforcement."""

import base64
import binascii
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException, status
from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError, OperationFailure

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile
from registry_pkgs.models import PrincipalType, SkillSource
from registry_pkgs.models.enums import RoleBits
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.oauth.user_service import UserService

from ..constants import (
    MAX_SKILL_FILE_COUNT,
    MAX_SKILL_FILE_RELATIVE_PATH_LENGTH,
    MAX_SKILL_FILE_SIZE,
    MAX_SKILL_FILES_TOTAL_SIZE,
    RESERVED_SKILL_FILE_NAMES,
)
from ..models.skill_frontmatter import (
    ClaudeCodeSkillFrontmatter,
    dump_claude_code_frontmatter,
    parse_claude_code_frontmatter,
)
from ..schemas.acl_schema import ResourcePermissions
from ..schemas.skill_api_schemas import (
    SkillCreateRequest,
    SkillFileContentResponse,
    SkillFileInput,
    SkillFileMetadataResponse,
    SkillFileResponse,
    SkillFileUpsertRequest,
    SkillUpdateRequest,
)
from ..utils.skill_files import guess_mime_type, is_text_content
from .access_control_service import ACLService

logger = logging.getLogger(__name__)

_REGISTRY_FILE_SOURCE = "registry-inline"
_UNAVAILABLE_FILE_REASON = "File content is not available in Registry because it was created in Jarvis Chat."
_MONGO_WRITE_CONFLICT_CODE = 112


def _is_write_conflict(exc: OperationFailure) -> bool:
    """Detect MongoDB WriteConflict so racing writers can be surfaced as 409 instead of 500."""
    return exc.code == _MONGO_WRITE_CONFLICT_CODE or "WriteConflict" in (exc.details or {}).get("errorLabels", [])


@dataclass(frozen=True)
class _PreparedFile:
    """A validated supporting file ready to persist as a registry-inline SkillFile."""

    relative_path: str
    raw: bytes
    mime_type: str
    is_binary: bool
    is_executable: bool


def _validate_relative_path(path: str) -> None:
    """Reject absolute paths, traversal, backslashes, non-normalized POSIX paths, and reserved names.

    The length check runs first so a caller can't bypass the schema-level cap by hitting the URL-based
    single-file endpoints (PUT/DELETE /skills/{id}/files/{path}), and so we cap the size of the path
    echoed back in any 422 error detail below.
    """
    if not path or not path.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="File path must not be empty")
    if len(path) > MAX_SKILL_FILE_RELATIVE_PATH_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File path exceeds {MAX_SKILL_FILE_RELATIVE_PATH_LENGTH} characters",
        )
    if "\\" in path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File path must not contain backslashes: {path!r}",
        )
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File path must not contain control characters: {path!r}",
        )
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"File path must be relative: {path!r}"
        )
    if not pure.parts or str(pure) != path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File path must be a normalized POSIX path: {path!r}",
        )
    if any(part in ("..", ".") for part in pure.parts):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File path must not contain '.' or '..' segments: {path!r}",
        )
    if path.lower() in RESERVED_SKILL_FILE_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"'{path}' is reserved and cannot be a supporting file",
        )


def _prepare_inline_file(relative_path: str, data: SkillFileInput | SkillFileUpsertRequest) -> _PreparedFile:
    """Validate one file's path and content, returning normalized bytes/metadata."""
    _validate_relative_path(relative_path)
    if data.body is not None:
        try:
            raw = base64.b64decode(data.body, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid base64 content for {relative_path!r}",
            ) from e
    else:
        raw = (data.content or "").encode("utf-8")
    if len(raw) > MAX_SKILL_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File {relative_path!r} exceeds the {MAX_SKILL_FILE_SIZE}-byte limit",
        )
    actual_is_binary = not is_text_content(raw)
    if data.content is not None and actual_is_binary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"File {relative_path!r} was sent as text but its content is binary; use 'body' (base64)",
        )
    if data.isBinary is not None and data.isBinary != actual_is_binary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Declared isBinary={data.isBinary} does not match actual content for {relative_path!r}",
        )
    return _PreparedFile(
        relative_path=relative_path,
        raw=raw,
        mime_type=data.mimeType or guess_mime_type(relative_path),
        is_binary=actual_is_binary,
        is_executable=data.isExecutable,
    )


def _validate_files_batch(files: list[SkillFileInput]) -> list[_PreparedFile]:
    """Validate a batch of inline files: per-file rules plus dedupe, count, and total-size caps."""
    if len(files) > MAX_SKILL_FILE_COUNT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"A skill may have at most {MAX_SKILL_FILE_COUNT} files",
        )
    prepared: list[_PreparedFile] = []
    seen: set[str] = set()
    total = 0
    for file_input in files:
        if file_input.relativePath in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Duplicate file path: {file_input.relativePath!r}",
            )
        seen.add(file_input.relativePath)
        prepared_file = _prepare_inline_file(file_input.relativePath, file_input)
        total += len(prepared_file.raw)
        prepared.append(prepared_file)
    if total > MAX_SKILL_FILES_TOTAL_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Total file size exceeds the {MAX_SKILL_FILES_TOTAL_SIZE}-byte limit",
        )
    return prepared


async def _count_and_other_total_bytes(
    skill_id: PydanticObjectId,
    excluded_relative_path: str,
    session: AsyncClientSession,
) -> tuple[int, int]:
    """Return (total file count, byte total excluding one path) via a server-side $group/$sum.

    Avoids pulling full SkillFile documents (including `body`) into Python just to sum sizes.
    """
    pipeline = [
        {"$match": {"skillId": skill_id}},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "otherTotal": {"$sum": {"$cond": [{"$eq": ["$relativePath", excluded_relative_path]}, 0, "$bytes"]}},
            }
        },
    ]
    results = await SkillFile.aggregate(pipeline, session=session).to_list()
    if not results:
        return 0, 0
    return results[0]["count"], results[0]["otherTotal"]


def _build_inline_skill_file(skill_id: PydanticObjectId, prepared: _PreparedFile, now: datetime) -> SkillFile:
    return SkillFile(
        skillId=skill_id,
        relativePath=prepared.relative_path,
        source=_REGISTRY_FILE_SOURCE,
        mimeType=prepared.mime_type,
        bytes=len(prepared.raw),
        content=None,
        body=prepared.raw,
        isBinary=prepared.is_binary,
        isExecutable=prepared.is_executable,
        createdAt=now,
        updatedAt=now,
    )


def _build_frontmatter(
    name: str,
    description: str,
    raw_frontmatter: dict[str, Any],
) -> ClaudeCodeSkillFrontmatter:
    return parse_claude_code_frontmatter(
        {
            **raw_frontmatter,
            "name": name,
            "description": description,
        }
    )


def _require_user_id(user_id: str | None) -> PydanticObjectId:
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


def _registry_file_text(skill_file: SkillFile) -> str | None:
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

    text_content = _registry_file_text(skill_file)
    binary_body = None
    if text_content is None and skill_file.body is not None:
        binary_body = base64.b64encode(skill_file.body).decode("ascii")

    return SkillFileResponse(
        relativePath=skill_file.relativePath,
        content=text_content,
        body=binary_body,
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

    @staticmethod
    def _deduplicate_by_name(
        skills_with_permissions: list[tuple[Skill, ResourcePermissions]],
        user_id: PydanticObjectId,
    ) -> list[tuple[Skill, ResourcePermissions]]:
        by_name: dict[str, list[tuple[Skill, ResourcePermissions]]] = {}
        for item in skills_with_permissions:
            by_name.setdefault(item[0].name, []).append(item)

        result: list[tuple[Skill, ResourcePermissions]] = []
        for name, group in by_name.items():
            if len(group) == 1:
                result.append(group[0])
                continue

            owned = [item for item in group if item[0].author == user_id]
            duplicate_ids = [str(item[0].id) for item in group]

            if owned:
                winner = owned[0]
                logger.warning(
                    "Skill name '%s' has %d duplicates (ids=%s); keeping author-matched skill id=%s for user %s",
                    name,
                    len(group),
                    duplicate_ids,
                    str(winner[0].id),
                    str(user_id),
                )
                result.append(winner)
            else:
                logger.error(
                    "Skill name '%s' has %d duplicates (ids=%s) but none "
                    "authored by user %s; excluding all from response",
                    name,
                    len(group),
                    duplicate_ids,
                    str(user_id),
                )

        return result

    async def list_skills(
        self,
        user_id: str | None,
        *,
        enabled: bool | None = None,
        file_count: int | None = None,
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
        paired = [(skill, permissions[skill.id]) for skill in skills if skill.id is not None]
        return self._deduplicate_by_name(paired, object_user_id)

    async def get_skill(
        self,
        skill_id: PydanticObjectId,
        user_id: str | None,
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
        user_id: str | None,
    ) -> tuple[Skill, list[SkillFileResponse]]:
        skill, files, _permissions = await self.get_skill(skill_id, user_id)
        return skill, [_sync_file_response(skill_file) for skill_file in files]

    async def get_skill_file_content(
        self,
        skill_id: PydanticObjectId,
        relative_path: str,
        user_id: str | None,
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
        user_id: str | None,
        author_name: str | None,
    ) -> tuple[Skill, list[SkillFile], ResourcePermissions]:
        object_user_id = _require_user_id(user_id)
        user = await self.user_service.get_user_by_user_id(str(object_user_id))
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user was not found")
        resolved_author_name = str(user.name or user.username or author_name or "").strip()
        if not resolved_author_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill author name is required")

        prepared_files = _validate_files_batch(data.files)

        duplicate_query = {
            "name": data.name,
            "author": object_user_id,
        }
        if await Skill.find_one(duplicate_query):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A skill with this name already exists")

        try:
            validated_frontmatter = _build_frontmatter(data.name, data.description, data.frontmatter)
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid frontmatter: {e.errors(include_url=False)}",
            ) from e

        now = datetime.now(UTC)
        skill = Skill(
            **data.model_dump(exclude={"frontmatter", "files"}),
            author=object_user_id,
            authorName=resolved_author_name,
            frontmatter=dump_claude_code_frontmatter(validated_frontmatter),
            disableModelInvocation=validated_frontmatter.disableModelInvocation,
            userInvocable=validated_frontmatter.userInvocable,
            allowedTools=validated_frontmatter.allowedTools,
            path=data.name,
            source=SkillSource.INLINE,
            enabled=True,
            createdByRegistry=True,
            fileCount=len(prepared_files),
            version=1,
            createdAt=now,
            updatedAt=now,
        )
        created_files: list[SkillFile] = []
        try:
            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    await skill.insert(session=mongo_session)
                    created_files = [_build_inline_skill_file(skill.id, prepared, now) for prepared in prepared_files]
                    if created_files:
                        insert_result = await SkillFile.insert_many(created_files, session=mongo_session)
                        for skill_file, inserted_id in zip(created_files, insert_result.inserted_ids, strict=True):
                            skill_file.id = PydanticObjectId(inserted_id)
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

        return skill, created_files, ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)

    async def upsert_skill_file(
        self,
        skill_id: PydanticObjectId,
        relative_path: str,
        data: SkillFileUpsertRequest,
        user_id: str | None,
    ) -> tuple[SkillFileMetadataResponse, bool]:
        object_user_id = _require_user_id(user_id)
        prepared = _prepare_inline_file(relative_path, data)  # fail fast before opening a session
        now = datetime.now(UTC)
        try:
            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    # Load skill INSIDE the transaction so a concurrent delete cannot orphan the SkillFile
                    # we're about to write via a stale in-memory reference.
                    skill = await Skill.find_one({"_id": skill_id}, session=mongo_session)
                    if skill is None:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
                    await self.acl_service.check_user_permission(
                        user_id=object_user_id,
                        resource_type=RegistryResourceType.SKILL.value,
                        resource_id=skill_id,
                        required_permission="EDIT",
                        session=mongo_session,
                    )
                    if not skill.createdByRegistry:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Only skills created in Registry can have their files modified",
                        )
                    existing = await SkillFile.find_one(
                        {"skillId": skill_id, "relativePath": relative_path}, session=mongo_session
                    )
                    if existing is not None and existing.source != _REGISTRY_FILE_SOURCE:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Cannot overwrite a file that was not created in Registry",
                        )
                    created = existing is None
                    existing_count, other_total = await _count_and_other_total_bytes(
                        skill_id, relative_path, session=mongo_session
                    )
                    if created and existing_count >= MAX_SKILL_FILE_COUNT:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"A skill may have at most {MAX_SKILL_FILE_COUNT} files",
                        )
                    if other_total + len(prepared.raw) > MAX_SKILL_FILES_TOTAL_SIZE:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Total file size exceeds the {MAX_SKILL_FILES_TOTAL_SIZE}-byte limit",
                        )
                    if existing is not None:
                        # existing.source is already _REGISTRY_FILE_SOURCE here (checked above).
                        existing.mimeType = prepared.mime_type
                        existing.bytes = len(prepared.raw)
                        existing.content = None
                        existing.body = prepared.raw
                        existing.isBinary = prepared.is_binary
                        existing.isExecutable = prepared.is_executable
                        existing.updatedAt = now
                        await existing.save(session=mongo_session)
                        saved = existing
                    else:
                        saved = _build_inline_skill_file(skill_id, prepared, now)
                        await saved.insert(session=mongo_session)
                    skill.fileCount = existing_count + (1 if created else 0)
                    skill.version += 1
                    skill.updatedAt = now
                    await skill.save(session=mongo_session)
        except OperationFailure as e:
            if _is_write_conflict(e):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Skill was modified concurrently; please retry",
                ) from e
            raise
        return _file_metadata(saved), created

    async def delete_skill_file(
        self,
        skill_id: PydanticObjectId,
        relative_path: str,
        user_id: str | None,
    ) -> None:
        object_user_id = _require_user_id(user_id)
        _validate_relative_path(relative_path)
        now = datetime.now(UTC)
        try:
            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    skill = await Skill.find_one({"_id": skill_id}, session=mongo_session)
                    if skill is None:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
                    await self.acl_service.check_user_permission(
                        user_id=object_user_id,
                        resource_type=RegistryResourceType.SKILL.value,
                        resource_id=skill_id,
                        required_permission="EDIT",
                        session=mongo_session,
                    )
                    if not skill.createdByRegistry:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Only skills created in Registry can have their files modified",
                        )
                    existing = await SkillFile.find_one(
                        {"skillId": skill_id, "relativePath": relative_path}, session=mongo_session
                    )
                    if existing is None:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill file not found")
                    if existing.source != _REGISTRY_FILE_SOURCE:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Cannot delete a file that was not created in Registry",
                        )
                    await existing.delete(session=mongo_session)
                    skill.fileCount = await SkillFile.find({"skillId": skill_id}, session=mongo_session).count()
                    skill.version += 1
                    skill.updatedAt = now
                    await skill.save(session=mongo_session)
        except OperationFailure as e:
            if _is_write_conflict(e):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Skill was modified concurrently; please retry",
                ) from e
            raise

    async def update_skill(
        self,
        skill_id: PydanticObjectId,
        data: SkillUpdateRequest,
        user_id: str | None,
    ) -> tuple[Skill, list[SkillFile], ResourcePermissions]:
        object_user_id = _require_user_id(user_id)
        # _get_existing_skill loads the document via find_one, which seeds Beanie's
        # state management snapshot.  The subsequent save() therefore emits an
        # incremental $set instead of a full-document replace, preserving any
        # Chat-only fields that are not modelled on ExtendedSkill.
        skill = await self._get_existing_skill(skill_id)
        updates = data.model_dump(exclude_unset=True)
        frontmatter_update = updates.pop("frontmatter", None)
        _nullable_fields = {"displayTitle"}
        invalid_nulls = [k for k, v in updates.items() if v is None and k not in _nullable_fields]
        if invalid_nulls:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Fields cannot be null: {', '.join(sorted(invalid_nulls))}",
            )
        if not updates and frontmatter_update is None:
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
                            },
                            session=mongo_session,
                        )
                        if duplicate:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail="A skill with this name already exists",
                            )
                    validated_frontmatter = None
                    if frontmatter_update is not None:
                        try:
                            validated_frontmatter = _build_frontmatter(
                                updates.get("name", skill.name),
                                updates.get("description", skill.description),
                                frontmatter_update,
                            )
                        except ValidationError as e:
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail=f"Invalid frontmatter: {e.errors(include_url=False)}",
                            ) from e
                    for field_name, value in updates.items():
                        setattr(skill, field_name, value)
                    if "name" in updates:
                        skill.path = updates["name"]
                    if validated_frontmatter is not None:
                        skill.frontmatter = dump_claude_code_frontmatter(validated_frontmatter)
                        skill.disableModelInvocation = validated_frontmatter.disableModelInvocation
                        skill.userInvocable = validated_frontmatter.userInvocable
                        skill.allowedTools = validated_frontmatter.allowedTools
                    skill.version += 1
                    skill.updatedAt = datetime.now(UTC)
                    await skill.save(session=mongo_session)
        except DuplicateKeyError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="A skill with this name already exists"
            ) from e

        return skill, await self._list_skill_files(skill_id), permissions

    async def delete_skill(self, skill_id: PydanticObjectId, user_id: str | None) -> None:
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
        user_id: str | None,
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
        skill = await Skill.find_one({"_id": skill_id})
        if skill is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        return skill

    @staticmethod
    async def _list_skill_files(skill_id: PydanticObjectId) -> list[SkillFile]:
        files = await SkillFile.find({"skillId": skill_id}).to_list()
        return sorted(files, key=lambda skill_file: skill_file.relativePath)


def skill_file_metadata(skill_file: SkillFile) -> SkillFileMetadataResponse:
    """Convert an ODM file to API metadata without exposing stored content."""
    return _file_metadata(skill_file)
