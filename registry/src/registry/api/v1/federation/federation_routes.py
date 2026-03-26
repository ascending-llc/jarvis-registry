import logging
import math

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status

from registry_pkgs.models.enums import FederationStateMachine, FederationStatus

from ....auth.dependencies import CurrentUser
from ....deps import (
    get_federation_crud_service,
    get_federation_job_service,
    get_federation_sync_service,
)
from ....schemas.errors import ErrorCode, create_error_detail
from ....schemas.federation_api_schemas import (
    FederationCreateRequest,
    FederationDeleteResponse,
    FederationDetailResponse,
    FederationLastSyncResponse,
    FederationLastSyncSummaryResponse,
    FederationListItemResponse,
    FederationPagedResponse,
    FederationStatsResponse,
    FederationSyncJobResponse,
    FederationSyncRequest,
    FederationUpdateRequest,
)
from ....schemas.server_api_schemas import PaginationMetadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/federations", tags=["federations"])


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _raise_sync_error(exc: Exception) -> None:
    message = str(exc)
    if "not implemented yet" in message:
        raise HTTPException(
            status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
            detail=create_error_detail(ErrorCode.NOT_IMPLEMENTED, message),
        ) from exc

    if "Failed to list AgentCore runtimes" in message:
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=create_error_detail(ErrorCode.EXTERNAL_SERVICE_ERROR, message),
        ) from exc

    raise HTTPException(
        status_code=http_status.HTTP_400_BAD_REQUEST,
        detail=create_error_detail(ErrorCode.INVALID_REQUEST, message),
    ) from exc


def _to_job_response(job) -> FederationSyncJobResponse:
    return FederationSyncJobResponse(
        id=str(job.id),
        federationId=str(job.federationId),
        jobType=job.jobType,
        status=job.status,
        phase=job.phase.value if hasattr(job.phase, "value") else str(job.phase),
        startedAt=job.startedAt,
        finishedAt=job.finishedAt,
    )


def _to_stats_response(stats) -> FederationStatsResponse:
    if stats is None:
        return FederationStatsResponse()
    return FederationStatsResponse(
        mcpServerCount=int(getattr(stats, "mcpServerCount", 0) or 0),
        agentCount=int(getattr(stats, "agentCount", 0) or 0),
        toolCount=int(getattr(stats, "toolCount", 0) or 0),
        importedTotal=int(getattr(stats, "importedTotal", 0) or 0),
    )


def _to_last_sync_response(last_sync) -> FederationLastSyncResponse | None:
    if last_sync is None:
        return None

    summary = getattr(last_sync, "summary", None)
    summary_response = None
    if summary is not None:
        summary_response = FederationLastSyncSummaryResponse(
            discoveredMcpServers=int(getattr(summary, "discoveredMcpServers", 0) or 0),
            discoveredAgents=int(getattr(summary, "discoveredAgents", 0) or 0),
            createdMcpServers=int(getattr(summary, "createdMcpServers", 0) or 0),
            updatedMcpServers=int(getattr(summary, "updatedMcpServers", 0) or 0),
            deletedMcpServers=int(getattr(summary, "deletedMcpServers", 0) or 0),
            unchangedMcpServers=int(getattr(summary, "unchangedMcpServers", 0) or 0),
            createdAgents=int(getattr(summary, "createdAgents", 0) or 0),
            updatedAgents=int(getattr(summary, "updatedAgents", 0) or 0),
            deletedAgents=int(getattr(summary, "deletedAgents", 0) or 0),
            unchangedAgents=int(getattr(summary, "unchangedAgents", 0) or 0),
            errors=int(getattr(summary, "errors", 0) or 0),
        )

    job_id = getattr(last_sync, "jobId", None)
    return FederationLastSyncResponse(
        jobId=str(job_id) if job_id is not None else None,
        jobType=getattr(last_sync, "jobType", None),
        status=getattr(last_sync, "status", None),
        startedAt=getattr(last_sync, "startedAt", None),
        finishedAt=getattr(last_sync, "finishedAt", None),
        summary=summary_response,
    )


def _to_list_item(item) -> FederationListItemResponse:
    return FederationListItemResponse(
        id=str(item.id),
        providerType=item.providerType,
        displayName=item.displayName,
        description=item.description,
        tags=item.tags,
        status=item.status,
        syncStatus=item.syncStatus,
        syncMessage=item.syncMessage,
        stats=_to_stats_response(item.stats),
        lastSync=_to_last_sync_response(item.lastSync),
        createdAt=item.createdAt,
        updatedAt=item.updatedAt,
    )


def _ensure_update_allowed(federation) -> None:
    if FederationStateMachine.can_update(federation.status):
        return
    raise HTTPException(
        status_code=http_status.HTTP_409_CONFLICT,
        detail=create_error_detail(
            ErrorCode.CONFLICT,
            f"Federation in status '{federation.status}' cannot be updated",
        ),
    )


def _ensure_sync_allowed(federation) -> None:
    if federation.status != FederationStatus.ACTIVE:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=create_error_detail(
                ErrorCode.CONFLICT,
                f"Federation in status '{federation.status}' cannot be synced",
            ),
        )

    if FederationStateMachine.can_start_sync(federation.syncStatus):
        return

    raise HTTPException(
        status_code=http_status.HTTP_409_CONFLICT,
        detail=create_error_detail(
            ErrorCode.CONFLICT,
            f"Federation in sync status '{federation.syncStatus}' cannot start a new sync",
        ),
    )


def _ensure_delete_allowed(federation) -> None:
    if FederationStateMachine.can_delete(federation.status):
        return
    raise HTTPException(
        status_code=http_status.HTTP_409_CONFLICT,
        detail=create_error_detail(
            ErrorCode.CONFLICT,
            f"Federation in status '{federation.status}' cannot be deleted",
        ),
    )


@router.post(
    "",
    response_model=FederationDetailResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_federation(
    data: FederationCreateRequest,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
):
    """
    Create a federation definition only.

    main logic:
        1.Create a new Federation document with:
            status = active
            syncStatus = idle
        2. Save the Federation document.
    """
    user_id = user_context.get("user_id")

    try:
        federation = await federation_crud_service.create_federation(
            provider_type=data.providerType,
            display_name=data.displayName,
            description=data.description,
            tags=data.tags,
            provider_config=data.providerConfig,
            created_by=user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, str(exc)),
        ) from exc
    logger.info(f"Created federation {federation.id}")
    recent_jobs = await federation_crud_service.get_recent_jobs(federation.id, limit=10)

    return FederationDetailResponse(
        id=str(federation.id),
        providerType=federation.providerType,
        displayName=federation.displayName,
        description=federation.description,
        tags=federation.tags,
        status=federation.status,
        syncStatus=federation.syncStatus,
        syncMessage=federation.syncMessage,
        providerConfig=federation.providerConfig,
        stats=_to_stats_response(federation.stats),
        lastSync=_to_last_sync_response(federation.lastSync),
        recentJobs=[_to_job_response(j) for j in recent_jobs],
        version=federation.version,
        createdBy=federation.createdBy,
        updatedBy=federation.updatedBy,
        createdAt=federation.createdAt,
        updatedAt=federation.updatedAt,
    )


@router.get("", response_model=FederationPagedResponse)
async def list_federations(
    providerType: str | None = Query(default=None),
    syncStatus: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    query: str | None = Query(default=None),
    keyword: str | None = Query(default=None, include_in_schema=False),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    pageSize: int | None = Query(default=None, ge=1, le=100, include_in_schema=False),
    federation_crud_service=Depends(get_federation_crud_service),
):
    """
    List federations.
    Args:
        providerType:
        syncStatus:
        tag:
        tags:
        query:
        keyword:
        page:
        per_page:
        pageSize:
        federation_crud_service:

    Returns:

    """
    search_query = query if query is not None else keyword
    effective_per_page = pageSize if pageSize is not None else per_page

    items, total = await federation_crud_service.list_federations(
        provider_type=providerType,
        sync_status=syncStatus,
        tag=tag,
        tags=tags,
        keyword=search_query,
        page=page,
        page_size=effective_per_page,
    )
    total_pages = math.ceil(total / effective_per_page) if total > 0 else 0
    return FederationPagedResponse(
        federations=[_to_list_item(x) for x in items],
        pagination=PaginationMetadata(
            total=total,
            page=page,
            perPage=effective_per_page,
            totalPages=total_pages,
        ),
    )


@router.get("/{federation_id}", response_model=FederationDetailResponse)
async def get_federation(
    federation_id: str,
    federation_crud_service=Depends(get_federation_crud_service),
):
    """
    Get a federation.
    Args:
        federation_id:
        federation_crud_service:

    Returns:

    """
    federation = await federation_crud_service.get_federation(federation_id)
    if not federation:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.NOT_FOUND, "Federation not found"),
        )

    recent_jobs = await federation_crud_service.get_recent_jobs(federation.id, limit=10)

    return FederationDetailResponse(
        id=str(federation.id),
        providerType=federation.providerType,
        displayName=federation.displayName,
        description=federation.description,
        tags=federation.tags,
        status=federation.status,
        syncStatus=federation.syncStatus,
        syncMessage=federation.syncMessage,
        providerConfig=federation.providerConfig,
        stats=_to_stats_response(federation.stats),
        lastSync=_to_last_sync_response(federation.lastSync),
        recentJobs=[_to_job_response(j) for j in recent_jobs],
        version=federation.version,
        createdBy=federation.createdBy,
        updatedBy=federation.updatedBy,
        createdAt=federation.createdAt,
        updatedAt=federation.updatedAt,
    )


@router.put("/{federation_id}", response_model=FederationDetailResponse)
async def update_federation(
    federation_id: str,
    data: FederationUpdateRequest,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    federation_job_service=Depends(get_federation_job_service),
    federation_sync_service=Depends(get_federation_sync_service),
):
    """
    Update a federation.
    Args:
        federation_id:
        data:
        user_context:
        federation_crud_service:
        federation_job_service:
        federation_sync_service:

    Returns:

    """
    federation = await federation_crud_service.get_federation(federation_id)
    if not federation:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.NOT_FOUND, "Federation not found"),
        )
    _ensure_update_allowed(federation)

    old_provider_config = dict(federation.providerConfig or {})
    user_id = user_context.get("user_id")
    need_resync = data.syncAfterUpdate and old_provider_config != data.providerConfig

    if need_resync:
        active_job = await federation_job_service.get_active_job(federation.id)
        if active_job:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=create_error_detail(ErrorCode.CONFLICT, "Federation already has an active sync job"),
            )

    try:
        federation = await federation_crud_service.update_federation(
            federation=federation,
            display_name=data.displayName,
            description=data.description,
            tags=data.tags,
            provider_config=data.providerConfig,
            version=data.version,
            updated_by=user_id,
        )
    except ValueError as exc:
        logger.error(f"Failed to update federation {federation_id}: {exc}")
        status_code = (
            http_status.HTTP_409_CONFLICT
            if str(exc) == "Federation version conflict"
            else http_status.HTTP_400_BAD_REQUEST
        )
        error_code = ErrorCode.CONFLICT if status_code == http_status.HTTP_409_CONFLICT else ErrorCode.INVALID_REQUEST
        raise HTTPException(
            status_code=status_code,
            detail=create_error_detail(error_code, str(exc)),
        ) from exc

    if need_resync:
        job = await federation_job_service.create_job(
            federation_id=federation.id,
            job_type="config_resync",
            trigger_type="api",
            triggered_by=user_id,
            request_snapshot={
                "providerType": _enum_value(federation.providerType),
                "providerConfig": federation.providerConfig,
            },
        )
        federation = await federation_crud_service.mark_sync_pending(federation)
        try:
            await federation_sync_service.run_sync(
                federation=federation,
                job=job,
                user_id=user_id,
            )
        except Exception as exc:
            _raise_sync_error(exc)
        logger.info(f"Updated federation {federation_id}: {federation},job: {job}")
    recent_jobs = await federation_crud_service.get_recent_jobs(federation.id, limit=10)
    return FederationDetailResponse(
        id=str(federation.id),
        providerType=federation.providerType,
        displayName=federation.displayName,
        description=federation.description,
        tags=federation.tags,
        status=federation.status,
        syncStatus=federation.syncStatus,
        syncMessage=federation.syncMessage,
        providerConfig=federation.providerConfig,
        stats=_to_stats_response(federation.stats),
        lastSync=_to_last_sync_response(federation.lastSync),
        recentJobs=[_to_job_response(j) for j in recent_jobs],
        version=federation.version,
        createdBy=federation.createdBy,
        updatedBy=federation.updatedBy,
        createdAt=federation.createdAt,
        updatedAt=federation.updatedAt,
    )


@router.post("/{federation_id}/sync", response_model=FederationSyncJobResponse)
async def sync_federation(
    federation_id: str,
    data: FederationSyncRequest,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    federation_job_service=Depends(get_federation_job_service),
    federation_sync_service=Depends(get_federation_sync_service),
):
    """
        sync a federation.
    Args:
        federation_id:
        data:
        user_context:
        federation_crud_service:
        federation_job_service:
        federation_sync_service:

    Returns: FederationSyncJobResponse

    main logic:
        1. Validate Request
        2. Create Sync Job
            Create FederationSyncJob
            Set:
                jobType = full_sync / force_sync
                status = pending
                Update Federation:
                syncStatus = pending
        3. Dispatch by Provider
            Route based on:
            federation.providerType
                AWS → AwsAgentCoreSyncHandler
                Azure → AzureAiFoundrySyncHandler
        4. Discovery
            Call provider API
            Get:
                MCP servers
                A2A agents
        5. Diff
            Compare:
            remote resources vs local resources
            (by remoteResourceId)
            Determine:
                create
                update
                delete (stale)
        6. Apply (Transaction)
            Inside transaction:
                upsert ExtendedMCPServer
                upsert A2AAgent
                delete stale resources
        7. Update Result
            Update Federation:
            syncStatus = success
                stats
                lastSync
                Update Job:
                status = success
    """
    federation = await federation_crud_service.get_federation(federation_id)
    if not federation:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.NOT_FOUND, "Federation not found"),
        )
    _ensure_sync_allowed(federation)
    try:
        federation_crud_service.validate_provider_config(federation.providerType, federation.providerConfig)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, str(exc)),
        ) from exc

    logger.info(f"sync federation {federation.id}, {federation.providerType}")
    active_job = await federation_job_service.get_active_job(federation.id)
    if active_job:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=create_error_detail(ErrorCode.CONFLICT, "Federation already has an active sync job"),
        )

    job_type = "force_sync" if data.force else "full_sync"
    job = await federation_job_service.create_job(
        federation_id=federation.id,
        job_type=job_type,
        trigger_type="manual",
        triggered_by=user_context.get("user_id"),
        request_snapshot={
            "providerType": _enum_value(federation.providerType),
            "providerConfig": federation.providerConfig,
            "reason": data.reason,
        },
    )
    await federation_crud_service.mark_sync_pending(federation)
    try:
        await federation_sync_service.run_sync(
            federation=federation,
            job=job,
            user_id=user_context.get("user_id"),
        )
    except Exception as exc:
        _raise_sync_error(exc)
    return _to_job_response(job)


@router.delete("/{federation_id}", response_model=FederationDeleteResponse)
async def delete_federation(
    federation_id: str,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    federation_job_service=Depends(get_federation_job_service),
    federation_sync_service=Depends(get_federation_sync_service),
):
    """
        Trigger delete job and remove all attached MCP/A2A resources.
    Args:
        federation_id:
        user_context:
        federation_crud_service:
        federation_job_service:
        federation_sync_service:

    Returns:

    """
    federation = await federation_crud_service.get_federation(federation_id)
    if not federation:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.NOT_FOUND, "Federation not found"),
        )
    _ensure_delete_allowed(federation)

    active_job = await federation_job_service.get_active_job(federation.id)
    if active_job:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=create_error_detail(ErrorCode.CONFLICT, "Federation already has an active job"),
        )

    await federation_crud_service.mark_deleting(federation)

    job = await federation_job_service.create_job(
        federation_id=federation.id,
        job_type="delete_sync",
        trigger_type="manual",
        triggered_by=user_context.get("user_id"),
        request_snapshot={
            "providerType": _enum_value(federation.providerType),
            "providerConfig": federation.providerConfig,
        },
    )

    try:
        await federation_sync_service.run_delete(federation=federation, job=job)
    except Exception as exc:
        _raise_sync_error(exc)

    return FederationDeleteResponse(
        federationId=str(federation.id),
        jobId=str(job.id),
        status="deleted",
    )
