import logging

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException

from ....auth.dependencies import CurrentUser
from ....schemas.skill_api_schemas import (
    SkillContentResponse,
    SkillFileResponse,
    SkillListResponse,
    SkillMetadataResponse,
)
from ....services.skill_service import (
    SkillContentUnavailableError,
    get_skill_with_files,
    list_skills,
)

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get(
    "/skills",
    response_model=SkillListResponse,
    summary="List skills",
    description="Return metadata for all body-only skills (fileCount == 0).",
)
async def list_skills_route(
    user_context: CurrentUser = None,
) -> SkillListResponse:
    try:
        skills = await list_skills()
    except HTTPException:
        raise
    except SkillContentUnavailableError as e:
        logger.warning("Skill content is unavailable while listing skills: %s", e)
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to list skills")
        raise HTTPException(status_code=500, detail="Internal server error") from e

    items: list[SkillMetadataResponse] = []
    for skill in skills:
        items.append(
            SkillMetadataResponse(
                id=str(skill.id),
                name=skill.name,
                description=skill.description,
                category=skill.category,
                tags=skill.tags,
                path=skill.path or skill.name,
                version=skill.version,
                fileCount=skill.fileCount,
                alwaysApply=skill.alwaysApply,
                contentHash=skill.contentHash,
                updatedAt=skill.updatedAt,
                deletedAt=skill.deletedAt,
            )
        )

    return SkillListResponse(skills=items)


@router.get(
    "/skills/{skill_id}/content",
    response_model=SkillContentResponse,
    summary="Get skill content",
    description="Return the full skill body and all associated files.",
)
async def get_skill_content(
    skill_id: PydanticObjectId,
    user_context: CurrentUser = None,
) -> SkillContentResponse:
    try:
        skill, skill_files = await get_skill_with_files(skill_id)
    except HTTPException:
        raise
    except SkillContentUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Failed to get skill content for %s", skill_id)
        raise HTTPException(status_code=500, detail="Internal server error") from e

    files = [
        SkillFileResponse(
            relativePath=sf.relativePath,
            content=sf.content,
            mimeType=sf.mimeType,
            bytes=sf.bytes,
            isBinary=sf.isBinary,
            isExecutable=sf.isExecutable,
        )
        for sf in sorted(skill_files, key=lambda f: f.relativePath)
    ]

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
        contentHash=skill.contentHash,
        files=files,
    )
