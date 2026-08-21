import logging
import math
from typing import NoReturn
from urllib.parse import urlencode

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from fastapi.responses import RedirectResponse

from registry_pkgs.models.enums import (
    SkillSyncStateMachine,
)
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.skill_sync_job import SkillSyncJob
from registry_pkgs.models.skill_sync_source import SkillSyncSource, SkillSyncSourceLastSync

from ....auth.dependencies import CurrentUser
from ....core.config import settings
from ....deps import (
    get_acl_service,
    get_skill_sync_job_service,
    get_skill_sync_service,
    get_skill_sync_source_crud_service,
    get_skill_sync_token_service,
)
from ....schemas.acl_schema import ResourcePermissions
from ....schemas.server_api_schemas import PaginationMetadata
from ....schemas.skill_sync_api_schemas import (
    SkillSyncDeleteResponse,
    SkillSyncJobResponse,
    SkillSyncSourceCreateRequest,
    SkillSyncSourceDetailResponse,
    SkillSyncSourceLastSyncResponse,
    SkillSyncSourceListItemResponse,
    SkillSyncSourcePagedResponse,
    SkillSyncSourceStatsResponse,
    SkillSyncSourceUpdateRequest,
    SkillSyncTriggerResponse,
)
from ....services.access_control_service import ACLService
from ....services.skill_sync_job_service import SkillSyncJobService
from ....services.skill_sync_service import (
    SKILL_SYNC_EXECUTION_UNAVAILABLE_DETAIL,
    SkillSyncService,
)
from ....services.skill_sync_source_crud_service import SkillSyncSourceCrudService
from ....services.skill_sync_token_service import SkillSyncTokenService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skill-sync-sources", tags=["skill-sync-sources"])


def _to_job_response(job: SkillSyncJob) -> SkillSyncJobResponse:
    return SkillSyncJobResponse(
        id=str(job.id),
        sourceId=str(job.sourceId),
        jobType=job.jobType,
        triggerType=job.triggerType,
        status=job.status,
        phase=job.phase,
        requestSnapshot=job.requestSnapshot,
        discoverySummary=job.discoverySummary.model_dump(mode="json"),
        applySummary=job.applySummary.model_dump(mode="json"),
        skillErrors=[item.model_dump(mode="json") for item in job.skillErrors],
        errorCode=job.errorCode,
        error=job.error,
        startedAt=job.startedAt,
        finishedAt=job.finishedAt,
        createdAt=job.createdAt,
        updatedAt=job.updatedAt,
    )


def _to_last_sync_response(value: SkillSyncSourceLastSync | None) -> SkillSyncSourceLastSyncResponse | None:
    if value is None:
        return None
    return SkillSyncSourceLastSyncResponse(
        jobId=str(value.jobId),
        status=value.status,
        startedAt=value.startedAt,
        finishedAt=value.finishedAt,
        commitSha=value.commitSha,
    )


def _to_list_response(
    source: SkillSyncSource,
    permissions: ResourcePermissions | None = None,
) -> SkillSyncSourceListItemResponse:
    return SkillSyncSourceListItemResponse(
        id=str(source.id),
        providerType=source.providerType,
        displayName=source.displayName,
        description=source.description,
        tags=source.tags,
        owner=source.owner,
        repo=source.repo,
        ref=source.ref,
        paths=source.paths,
        skillDiscoveryDepth=source.skillDiscoveryDepth,
        status=source.status,
        syncStatus=source.syncStatus,
        syncMessage=source.syncMessage,
        stats=SkillSyncSourceStatsResponse.model_validate(source.stats),
        lastSync=_to_last_sync_response(source.lastSync),
        permissions=permissions,
        createdAt=source.createdAt,
        updatedAt=source.updatedAt,
    )


async def _to_detail_response(
    source: SkillSyncSource,
    source_service: SkillSyncSourceCrudService,
    permissions: ResourcePermissions | None = None,
) -> SkillSyncSourceDetailResponse:
    recent_jobs = await source_service.get_recent_jobs(source.id)
    base = _to_list_response(source, permissions)
    return SkillSyncSourceDetailResponse(
        **base.model_dump(),
        githubAppClientId=source.githubAppClientId,
        hasClientSecret=bool(source.githubAppClientSecretEncrypted),
        recentJobs=[_to_job_response(job) for job in recent_jobs],
        createdBy=source.createdBy,
        updatedBy=source.updatedBy,
    )


async def _required_source(source_id: str, source_service: SkillSyncSourceCrudService) -> SkillSyncSource:
    source = await source_service.get_source(source_id)
    if source is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Skill sync source not found")
    return source


async def _authorize_source_and_raise_unavailable(
    source_id: str,
    user_context: CurrentUser,
    required_permission: str,
    *,
    source_service: SkillSyncSourceCrudService,
    acl_service: ACLService,
) -> NoReturn:
    user_object_id = PydanticObjectId(user_context["user_id"])
    source = await _required_source(source_id, source_service)
    await acl_service.check_user_permission(
        user_id=user_object_id,
        resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
        resource_id=source.id,
        required_permission=required_permission,
    )
    raise HTTPException(
        status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
        detail=SKILL_SYNC_EXECUTION_UNAVAILABLE_DETAIL,
    )


@router.post("", response_model=SkillSyncSourceDetailResponse, status_code=http_status.HTTP_201_CREATED)
async def create_source(
    data: SkillSyncSourceCreateRequest,
    user_context: CurrentUser,
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    skill_sync_service: SkillSyncService = Depends(get_skill_sync_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    user_str_id = str(user_context["user_id"])
    user_object_id = PydanticObjectId(user_context["user_id"])
    try:
        source = await skill_sync_service.create_source_with_owner_acl(
            display_name=data.displayName,
            description=data.description,
            tags=data.tags,
            owner=data.owner,
            repo=data.repo,
            ref=data.ref,
            paths=data.paths,
            skill_discovery_depth=data.skillDiscoveryDepth,
            github_app_client_id=data.githubAppClientId,
            github_app_client_secret=data.githubAppClientSecret,
            created_by=user_str_id,
            principal_id=user_object_id,
            acl_service=acl_service,
        )
        return await _to_detail_response(
            source,
            source_service,
            ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create skill sync source")
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


@router.get("", response_model=SkillSyncSourcePagedResponse)
async def list_sources(
    user_context: CurrentUser,
    syncStatus: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    query: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    try:
        user_object_id = PydanticObjectId(user_context["user_id"])
        accessible_ids = await acl_service.get_accessible_resource_ids(
            user_id=user_object_id,
            resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
        )
        items, total = await source_service.list_sources(
            sync_status=syncStatus,
            tag=tag,
            keyword=query,
            page=page,
            page_size=per_page,
            accessible_source_ids=accessible_ids,
        )
        permissions_by_id = await acl_service.get_user_permissions_for_resources(
            user_id=user_object_id,
            resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
            resource_ids=[source.id for source in items],
        )
        responses = [_to_list_response(source, permissions_by_id[source.id]) for source in items]
        return SkillSyncSourcePagedResponse(
            sources=responses,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                perPage=per_page,
                totalPages=math.ceil(total / per_page) if total else 0,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list skill sync sources")
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


@router.get("/{source_id}", response_model=SkillSyncSourceDetailResponse)
async def get_source(
    source_id: str,
    user_context: CurrentUser,
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    try:
        user_object_id = PydanticObjectId(user_context["user_id"])
        source = await _required_source(source_id, source_service)
        permissions = await acl_service.check_user_permission(
            user_id=user_object_id,
            resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
            resource_id=source.id,
            required_permission="VIEW",
        )
        return await _to_detail_response(source, source_service, permissions)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get skill sync source %s", source_id)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


@router.put("/{source_id}", response_model=SkillSyncSourceDetailResponse)
async def update_source(
    source_id: str,
    data: SkillSyncSourceUpdateRequest,
    user_context: CurrentUser,
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    token_service: SkillSyncTokenService = Depends(get_skill_sync_token_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    try:
        user_object_id = PydanticObjectId(user_context["user_id"])
        user_str_id = str(user_context["user_id"])
        source = await _required_source(source_id, source_service)
        permissions = await acl_service.check_user_permission(
            user_id=user_object_id,
            resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
            resource_id=source.id,
            required_permission="EDIT",
        )
        if not SkillSyncStateMachine.can_update(source.status):
            raise HTTPException(http_status.HTTP_409_CONFLICT, detail="Skill sync source cannot be updated")
        if data.syncAfterUpdate:
            raise HTTPException(
                status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                detail=SKILL_SYNC_EXECUTION_UNAVAILABLE_DETAIL,
            )
        changes = data.model_dump(exclude_unset=True, exclude={"syncAfterUpdate"})
        credentials_changed = "githubAppClientId" in changes or "githubAppClientSecret" in changes
        source = await source_service.update_source(source, changes, updated_by=user_str_id)
        if credentials_changed:
            await token_service.delete_source_tokens(source.id)
        return await _to_detail_response(source, source_service, permissions)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to update skill sync source %s", source_id)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


@router.delete("/{source_id}", response_model=SkillSyncDeleteResponse, status_code=http_status.HTTP_202_ACCEPTED)
async def delete_source(
    source_id: str,
    user_context: CurrentUser,
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    try:
        await _authorize_source_and_raise_unavailable(
            source_id,
            user_context,
            "DELETE",
            source_service=source_service,
            acl_service=acl_service,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete skill sync source %s", source_id)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


@router.post("/{source_id}/sync", response_model=SkillSyncTriggerResponse)
async def sync_source(
    source_id: str,
    user_context: CurrentUser,
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    try:
        await _authorize_source_and_raise_unavailable(
            source_id,
            user_context,
            "EDIT",
            source_service=source_service,
            acl_service=acl_service,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to trigger skill sync for %s", source_id)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


@router.get("/{source_id}/oauth/initiate")
async def initiate_skill_sync_oauth(
    source_id: str,
    user_context: CurrentUser,
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    try:
        await _authorize_source_and_raise_unavailable(
            source_id,
            user_context,
            "EDIT",
            source_service=source_service,
            acl_service=acl_service,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to initiate GitHub OAuth for %s", source_id)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


@router.get("/{source_id}/oauth/callback", name="skill_sync_oauth_callback")
async def skill_sync_oauth_callback(
    source_id: str,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
):
    error_redirect = (
        f"{settings.registry_client_url}/skill-sync-sources/{source_id}?{urlencode({'error': 'auth_failed'})}"
    )
    if error or not code or not state:
        return RedirectResponse(error_redirect)
    try:
        await _required_source(source_id, source_service)
        return RedirectResponse(
            f"{settings.registry_client_url}/skill-sync-sources/{source_id}?{urlencode({'error': 'sync_unavailable'})}"
        )
    except HTTPException:
        return RedirectResponse(error_redirect)
    except Exception:
        logger.exception("GitHub OAuth callback failed for skill sync source %s", source_id)
        return RedirectResponse(error_redirect)


@router.get("/{source_id}/jobs/{job_id}", response_model=SkillSyncJobResponse)
async def get_sync_job(
    source_id: str,
    job_id: str,
    user_context: CurrentUser,
    source_service: SkillSyncSourceCrudService = Depends(get_skill_sync_source_crud_service),
    job_service: SkillSyncJobService = Depends(get_skill_sync_job_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    try:
        user_object_id = PydanticObjectId(user_context["user_id"])
        source = await _required_source(source_id, source_service)
        await acl_service.check_user_permission(
            user_id=user_object_id,
            resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
            resource_id=source.id,
            required_permission="VIEW",
        )
        job = await job_service.get_job(job_id, source_id=source.id)
        if job is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="Skill sync job not found")
        return _to_job_response(job)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get skill sync job %s", job_id)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc
