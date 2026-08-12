"""Skill management and CLI sync-down routes."""

# ruff: noqa: UP045 -- Repository guidance requires explicit Optional[T] annotations.

import logging
from typing import Annotated, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ....auth.dependencies import CurrentUser
from ....deps import get_skill_service
from ....schemas.acl_schema import ResourcePermissions
from ....schemas.skill_api_schemas import (
    SkillContentResponse,
    SkillCreateRequest,
    SkillDetailResponse,
    SkillFileContentResponse,
    SkillListResponse,
    SkillMetadataResponse,
    SkillToggleRequest,
    SkillToggleResponse,
    SkillUpdateRequest,
)
from ....services.skill_service import SkillService, skill_file_metadata

logger = logging.getLogger(__name__)

router = APIRouter()


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
        deletedAt=skill.deletedAt,
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
async def list_skills_route(
    user_context: CurrentUser,
    enabled: Optional[bool] = None,
    file_count: Annotated[Optional[int], Query(alias="fileCount", ge=0)] = None,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillListResponse:
    try:
        results = await skill_service.list_skills(
            user_id=user_context.get("user_id"),
            enabled=enabled,
            file_count=file_count,
        )
        return SkillListResponse(skills=[_metadata_response(skill, permissions) for skill, permissions in results])
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list skills")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post(
    "/skills",
    response_model=SkillDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a skill",
)
async def create_skill(
    data: SkillCreateRequest,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillDetailResponse:
    try:
        skill, permissions = await skill_service.create_skill(
            data=data,
            user_id=user_context.get("user_id"),
            author_name=user_context.get("username"),
        )
        return _detail_response(skill, [], permissions)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create skill")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/skills/{skill_id}", response_model=SkillDetailResponse, summary="Get a skill")
async def get_skill(
    skill_id: PydanticObjectId,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillDetailResponse:
    try:
        skill, files, permissions = await skill_service.get_skill(skill_id, user_context.get("user_id"))
        return _detail_response(skill, files, permissions)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get skill %s", skill_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get(
    "/skills/{skill_id}/content",
    response_model=SkillContentResponse,
    summary="Get skill sync content",
)
async def get_skill_content(
    skill_id: PydanticObjectId,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillContentResponse:
    try:
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
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get skill content for %s", skill_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get(
    "/skills/{skill_id}/files/{file_path:path}",
    response_model=SkillFileContentResponse,
    summary="Get skill file content",
)
async def get_skill_file_content(
    skill_id: PydanticObjectId,
    file_path: str,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillFileContentResponse:
    try:
        return await skill_service.get_skill_file_content(skill_id, file_path, user_context.get("user_id"))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get file %s for skill %s", file_path, skill_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.patch("/skills/{skill_id}", response_model=SkillDetailResponse, summary="Update a skill")
async def update_skill(
    skill_id: PydanticObjectId,
    data: SkillUpdateRequest,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillDetailResponse:
    try:
        skill, files, permissions = await skill_service.update_skill(
            skill_id=skill_id,
            data=data,
            user_id=user_context.get("user_id"),
        )
        return _detail_response(skill, files, permissions)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update skill %s", skill_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a skill")
async def delete_skill(
    skill_id: PydanticObjectId,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> Response:
    try:
        await skill_service.delete_skill(skill_id, user_context.get("user_id"))
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete skill %s", skill_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/skills/{skill_id}/toggle", response_model=SkillToggleResponse, summary="Toggle a skill")
async def toggle_skill(
    skill_id: PydanticObjectId,
    data: SkillToggleRequest,
    user_context: CurrentUser,
    skill_service: SkillService = Depends(get_skill_service),
) -> SkillToggleResponse:
    try:
        skill = await skill_service.toggle_skill(skill_id, data.enabled, user_context.get("user_id"))
        return SkillToggleResponse(id=str(skill.id), enabled=skill.enabled)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to toggle skill %s", skill_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e
