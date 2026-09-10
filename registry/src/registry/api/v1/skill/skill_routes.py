"""Skill management and CLI sync-down routes."""

# ruff: noqa: UP045 -- Repository guidance requires explicit Optional[T] annotations.

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Optional, ParamSpec, TypeVar

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ....auth.dependencies import CurrentUser
from ....core.telemetry_decorators import track_registry_operation
from ....deps import get_skill_service
from ....schemas.acl_schema import ResourcePermissions
from ....schemas.errors import ErrorCode, create_error_detail
from ....schemas.skill_api_schemas import (
    SkillContentResponse,
    SkillCreateRequest,
    SkillDetailResponse,
    SkillFileContentResponse,
    SkillFileMetadataResponse,
    SkillFileUpsertRequest,
    SkillListResponse,
    SkillMetadataResponse,
    SkillToggleRequest,
    SkillToggleResponse,
    SkillUpdateRequest,
)
from ....services.skill_service import SkillService, skill_file_metadata

logger = logging.getLogger(__name__)

router = APIRouter()

_P = ParamSpec("_P")
_R = TypeVar("_R")


def handle_service_errors(operation: str) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Route-layer error boundary shared by every skills route.

    Re-raises HTTPException (4xx from the service layer) unchanged; logs and wraps any other
    exception into a uniform 500 response, per the repo's route-layer error handling strategy.
    """

    def decorator(func: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        @functools.wraps(func)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("Failed to %s", operation)
                raise HTTPException(
                    status_code=500,
                    detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error"),
                ) from e

        return wrapper

    return decorator


def _metadata_response(skill, permissions: ResourcePermissions) -> SkillMetadataResponse:
    return SkillMetadataResponse(
        id=str(skill.id),
        name=skill.name,
        displayTitle=skill.displayTitle,
        description=skill.description,
        category=skill.category,
        tags=skill.tags,
        path=skill.path or skill.name,
        version=skill.version,
        fileCount=skill.fileCount,
        alwaysApply=skill.alwaysApply,
        enabled=skill.enabled,
        author=str(skill.author),
        authorName=skill.authorName,
        source=skill.source,
        sourceMetadata=skill.sourceMetadata,
        createdByRegistry=skill.createdByRegistry,
        permissions=permissions,
        updatedAt=skill.updatedAt,
    )


def _detail_response(skill, files, permissions: ResourcePermissions) -> SkillDetailResponse:
    return SkillDetailResponse(
        id=str(skill.id),
        name=skill.name,
        displayTitle=skill.displayTitle,
        description=skill.description,
        body=skill.body,
        frontmatter=skill.frontmatter,
        category=skill.category,
        tags=skill.tags,
        version=skill.version,
        fileCount=skill.fileCount,
        enabled=skill.enabled,
        alwaysApply=skill.alwaysApply,
        userInvocable=skill.userInvocable,
        disableModelInvocation=skill.disableModelInvocation,
        allowedTools=skill.allowedTools,
        author=str(skill.author),
        authorName=skill.authorName,
        source=skill.source,
        sourceMetadata=skill.sourceMetadata,
        createdByRegistry=skill.createdByRegistry,
        createdAt=skill.createdAt,
        updatedAt=skill.updatedAt,
        files=[skill_file_metadata(skill_file) for skill_file in files],
        permissions=permissions,
    )


@router.get("/skills", response_model=SkillListResponse, summary="List skills")
@track_registry_operation("list", resource_type="skill")
@handle_service_errors("list skills")
async def list_skills_route(
    user_context: CurrentUser,
    enabled: Optional[bool] = None,
    file_count: Annotated[Optional[int], Query(alias="fileCount", ge=0)] = None,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillListResponse:
    results = await skill_service.list_skills(
        user_id=user_context.get("user_id"),
        enabled=enabled,
        file_count=file_count,
    )
    return SkillListResponse(skills=[_metadata_response(skill, permissions) for skill, permissions in results])


@router.post(
    "/skills",
    response_model=SkillDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a skill",
)
@track_registry_operation("create", resource_type="skill")
@handle_service_errors("create skill")
async def create_skill(
    data: SkillCreateRequest,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillDetailResponse:
    skill, files, permissions = await skill_service.create_skill(
        data=data,
        user_id=user_context.get("user_id"),
        author_name=user_context.get("username"),
    )
    return _detail_response(skill, files, permissions)


@router.get("/skills/{skill_id}", response_model=SkillDetailResponse, summary="Get a skill")
@track_registry_operation("read", resource_type="skill")
@handle_service_errors("get skill")
async def get_skill(
    skill_id: PydanticObjectId,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillDetailResponse:
    skill, files, permissions = await skill_service.get_skill(skill_id, user_context.get("user_id"))
    return _detail_response(skill, files, permissions)


@router.get(
    "/skills/{skill_id}/content",
    response_model=SkillContentResponse,
    summary="Get skill sync content",
)
@track_registry_operation("read", resource_type="skill_content")
@handle_service_errors("get skill content")
async def get_skill_content(
    skill_id: PydanticObjectId,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillContentResponse:
    skill, files = await skill_service.get_skill_with_files(skill_id, user_context.get("user_id"))
    return SkillContentResponse(
        id=str(skill.id),
        name=skill.name,
        description=skill.description,
        body=skill.body,
        frontmatter=skill.frontmatter,
        alwaysApply=skill.alwaysApply,
        disableModelInvocation=skill.disableModelInvocation,
        userInvocable=skill.userInvocable,
        allowedTools=skill.allowedTools,
        category=skill.category,
        createdByRegistry=skill.createdByRegistry,
        files=files,
    )


@router.get(
    "/skills/{skill_id}/files/{file_path:path}",
    response_model=SkillFileContentResponse,
    summary="Get skill file content",
)
@track_registry_operation("read", resource_type="skill_file")
@handle_service_errors("get skill file content")
async def get_skill_file_content(
    skill_id: PydanticObjectId,
    file_path: str,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillFileContentResponse:
    return await skill_service.get_skill_file_content(skill_id, file_path, user_context.get("user_id"))


@router.put(
    "/skills/{skill_id}/files/{file_path:path}",
    response_model=SkillFileMetadataResponse,
    summary="Create or replace a skill file",
)
@track_registry_operation("upsert", resource_type="skill_file")
@handle_service_errors("upsert skill file")
async def upsert_skill_file(
    skill_id: PydanticObjectId,
    file_path: str,
    data: SkillFileUpsertRequest,
    user_context: CurrentUser,
    response: Response,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillFileMetadataResponse:
    metadata, created = await skill_service.upsert_skill_file(skill_id, file_path, data, user_context.get("user_id"))
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return metadata


@router.delete(
    "/skills/{skill_id}/files/{file_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill file",
)
@track_registry_operation("delete", resource_type="skill_file")
@handle_service_errors("delete skill file")
async def delete_skill_file(
    skill_id: PydanticObjectId,
    file_path: str,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> Response:
    await skill_service.delete_skill_file(skill_id, file_path, user_context.get("user_id"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/skills/{skill_id}", response_model=SkillDetailResponse, summary="Update a skill")
@track_registry_operation("update", resource_type="skill")
@handle_service_errors("update skill")
async def update_skill(
    skill_id: PydanticObjectId,
    data: SkillUpdateRequest,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillDetailResponse:
    skill, files, permissions = await skill_service.update_skill(
        skill_id=skill_id,
        data=data,
        user_id=user_context.get("user_id"),
    )
    return _detail_response(skill, files, permissions)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a skill")
@track_registry_operation("delete", resource_type="skill")
@handle_service_errors("delete skill")
async def delete_skill(
    skill_id: PydanticObjectId,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> Response:
    await skill_service.delete_skill(skill_id, user_context.get("user_id"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/skills/{skill_id}/toggle", response_model=SkillToggleResponse, summary="Toggle a skill")
@track_registry_operation("toggle", resource_type="skill")
@handle_service_errors("toggle skill")
async def toggle_skill(
    skill_id: PydanticObjectId,
    data: SkillToggleRequest,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillToggleResponse:
    skill = await skill_service.toggle_skill(skill_id, data.enabled, user_context.get("user_id"))
    return SkillToggleResponse(id=str(skill.id), enabled=skill.enabled)
