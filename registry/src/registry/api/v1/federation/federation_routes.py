import logging
import math
from typing import NoReturn

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status

from registry_pkgs.database.decorators import use_transaction
from registry_pkgs.models import PrincipalType
from registry_pkgs.models.enums import FederationStateMachine, FederationStatus, RoleBits

from ....auth.dependencies import CurrentUser
from ....core.telemetry_decorators import track_registry_operation
from ....deps import (
    get_acl_service,
    get_federation_crud_service,
    get_federation_sync_service,
)
from ....schemas.acl_schema import ResourcePermissions
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
    FederationSyncDryRunResponse,
    FederationSyncDryRunSummaryResponse,
    FederationSyncJobResponse,
    FederationSyncRequest,
    FederationUpdateRequest,
)
from ....schemas.server_api_schemas import PaginationMetadata
from ....services.access_control_service import ACLService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/federations", tags=["federations"])
FEDERATION_RESOURCE_TYPE = "federation"


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _raise_sync_error(exc: Exception) -> NoReturn:
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
    if "Failed to list Azure AI Foundry agents" in message:
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=create_error_detail(ErrorCode.EXTERNAL_SERVICE_ERROR, message),
        ) from exc

    raise HTTPException(
        status_code=http_status.HTTP_400_BAD_REQUEST,
        detail=create_error_detail(ErrorCode.INVALID_REQUEST, message),
    ) from exc


def _raise_federation_value_error(exc: ValueError) -> NoReturn:
    message = str(exc)
    if "not implemented yet" in message:
        raise HTTPException(
            status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
            detail=create_error_detail(ErrorCode.NOT_IMPLEMENTED, message),
        ) from exc

    if message in {
        "Federation version conflict",
        "Federation already has an active sync job",
        "Federation already has an active job",
    }:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=create_error_detail(ErrorCode.CONFLICT, message),
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


def _to_dry_run_response(result) -> FederationSyncDryRunResponse:
    summary = getattr(result, "summary", None)
    return FederationSyncDryRunResponse(
        dryRun=True,
        providerType=result.provider_type,
        providerConfig=dict(getattr(result, "provider_config", {}) or {}),
        summary=FederationSyncDryRunSummaryResponse(
            discoveredMcpServers=int(getattr(result, "discovered_mcp_count", 0) or 0),
            discoveredAgents=int(getattr(result, "discovered_a2a_count", 0) or 0),
            createdMcpServers=int(getattr(summary, "createdMcpServers", 0) or 0),
            updatedMcpServers=int(getattr(summary, "updatedMcpServers", 0) or 0),
            deletedMcpServers=int(getattr(summary, "deletedMcpServers", 0) or 0),
            unchangedMcpServers=int(getattr(summary, "unchangedMcpServers", 0) or 0),
            createdAgents=int(getattr(summary, "createdAgents", 0) or 0),
            updatedAgents=int(getattr(summary, "updatedAgents", 0) or 0),
            deletedAgents=int(getattr(summary, "deletedAgents", 0) or 0),
            unchangedAgents=int(getattr(summary, "unchangedAgents", 0) or 0),
            skippedAgents=int(getattr(summary, "skippedAgents", 0) or 0),
            errors=int(getattr(summary, "errors", 0) or 0),
            errorMessages=list(getattr(summary, "errorMessages", []) or []),
        ),
        message=getattr(result, "message", None),
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
            errorMessages=list(getattr(summary, "errorMessages", []) or []),
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


def _to_list_item(item, permissions: ResourcePermissions | None = None) -> FederationListItemResponse:
    return FederationListItemResponse(
        id=str(item.id),
        providerType=item.providerType,
        displayName=item.displayName,
        description=item.description,
        tags=item.tags,
        status=item.status,
        syncStatus=item.syncStatus,
        syncMessage=item.syncMessage,
        stats=_to_stats_response(getattr(item, "stats", None)),
        lastSync=_to_last_sync_response(getattr(item, "lastSync", None)),
        permissions=permissions,
        createdAt=item.createdAt,
        updatedAt=item.updatedAt,
    )


def _to_paged_response(
    items, total: int, page: int, per_page: int, permissions_by_id: dict[str, ResourcePermissions] | None = None
) -> FederationPagedResponse:
    total_pages = math.ceil(total / per_page) if total > 0 else 0
    return FederationPagedResponse(
        federations=[_to_list_item(x, permissions_by_id.get(str(x.id)) if permissions_by_id else None) for x in items],
        pagination=PaginationMetadata(
            total=total,
            page=page,
            perPage=per_page,
            totalPages=total_pages,
        ),
    )


def _to_delete_response(federation, job) -> FederationDeleteResponse:
    return FederationDeleteResponse(
        federationId=str(federation.id),
        jobId=str(job.id),
        status="deleted",
    )


async def _get_required_federation(federation_id: str, federation_crud_service):
    federation = await federation_crud_service.get_federation(federation_id)
    if federation:
        return federation
    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=create_error_detail(ErrorCode.NOT_FOUND, "Federation not found"),
    )


async def _to_detail_response(
    federation,
    federation_crud_service,
    permissions: ResourcePermissions | None = None,
) -> FederationDetailResponse:
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
        stats=_to_stats_response(getattr(federation, "stats", None)),
        lastSync=_to_last_sync_response(getattr(federation, "lastSync", None)),
        recentJobs=[_to_job_response(j) for j in recent_jobs],
        permissions=permissions,
        version=federation.version,
        createdBy=federation.createdBy,
        updatedBy=federation.updatedBy,
        createdAt=federation.createdAt,
        updatedAt=federation.updatedAt,
    )


def _raise_conflict(message: str) -> None:
    raise HTTPException(
        status_code=http_status.HTTP_409_CONFLICT,
        detail=create_error_detail(ErrorCode.CONFLICT, message),
    )


@router.post(
    "",
    response_model=FederationDetailResponse,
    status_code=http_status.HTTP_201_CREATED,
)
@track_registry_operation("create", resource_type="federation")
@use_transaction
async def create_federation(
    data: FederationCreateRequest,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    acl_service: ACLService = Depends(get_acl_service),
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
        await acl_service.grant_permission(
            principal_type=PrincipalType.USER,
            principal_id=PydanticObjectId(user_id),
            resource_type=FEDERATION_RESOURCE_TYPE,
            resource_id=federation.id,
            perm_bits=RoleBits.OWNER,
        )
    except ValueError as exc:
        _raise_federation_value_error(exc)
    logger.info(f"Created federation {federation.id}")
    return await _to_detail_response(
        federation,
        federation_crud_service,
        permissions=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True),
    )


@router.get("", response_model=FederationPagedResponse)
@track_registry_operation("list", resource_type="federation")
async def list_federations(
    user_context: CurrentUser,
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
    acl_service: ACLService = Depends(get_acl_service),
):
    """
    List federations.
    """
    try:
        search_query = query if query is not None else keyword
        effective_per_page = pageSize if pageSize is not None else per_page
        user_id = user_context.get("user_id")
        accessible_ids = await acl_service.get_accessible_resource_ids(
            user_id=PydanticObjectId(user_id),
            resource_type=FEDERATION_RESOURCE_TYPE,
        )

        items, total = await federation_crud_service.list_federations(
            provider_type=providerType,
            sync_status=syncStatus,
            tag=tag,
            tags=tags,
            keyword=search_query,
            page=page,
            page_size=effective_per_page,
            accessible_federation_ids=accessible_ids,
        )
        permissions_by_id = {}
        for federation in items:
            permissions_by_id[str(federation.id)] = await acl_service.get_user_permissions_for_resource(
                user_id=PydanticObjectId(user_id),
                resource_type=FEDERATION_RESOURCE_TYPE,
                resource_id=federation.id,
            )
        return _to_paged_response(items, total, page, effective_per_page, permissions_by_id)
    except HTTPException:
        logger.exception("Failed to list federations due to HTTP exception")
        raise
    except Exception as exc:
        logger.exception("Unexpected error while listing federations")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(
                ErrorCode.INTERNAL_ERROR,
                "Internal server error",
            ),
        ) from exc


@router.get("/{federation_id}", response_model=FederationDetailResponse)
@track_registry_operation("read", resource_type="federation")
async def get_federation(
    federation_id: str,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """
    Get a federation.
    """
    try:
        federation = await _get_required_federation(federation_id, federation_crud_service)
        permissions = await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_context.get("user_id")),
            resource_type=FEDERATION_RESOURCE_TYPE,
            resource_id=federation.id,
            required_permission="VIEW",
        )
        return await _to_detail_response(federation, federation_crud_service, permissions)
    except HTTPException:
        logger.exception("Failed to get federation %s due to HTTP exception", federation_id)
        raise
    except Exception as exc:
        logger.exception("Unexpected error while getting federation %s", federation_id)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(
                ErrorCode.INTERNAL_ERROR,
                "Internal server error",
            ),
        ) from exc


@router.put("/{federation_id}", response_model=FederationDetailResponse)
@track_registry_operation("update", resource_type="federation")
async def update_federation(
    federation_id: str,
    data: FederationUpdateRequest,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    federation_sync_service=Depends(get_federation_sync_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """
    Update a federation.
    Returns:

    """
    federation = await _get_required_federation(federation_id, federation_crud_service)
    permissions = await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_context.get("user_id")),
        resource_type=FEDERATION_RESOURCE_TYPE,
        resource_id=federation.id,
        required_permission="EDIT",
    )
    if not FederationStateMachine.can_update(federation.status):
        _raise_conflict(f"Federation in status '{federation.status}' cannot be updated")

    try:
        federation, job = await federation_sync_service.update_federation_with_optional_resync(
            federation=federation,
            display_name=data.displayName,
            description=data.description,
            tags=data.tags,
            provider_config=data.providerConfig,
            version=data.version,
            updated_by=user_context.get("user_id"),
            sync_after_update=data.syncAfterUpdate,
        )
        if job is not None:
            logger.info(f"Updated federation {federation_id}: {federation},job: {job}")
    except ValueError as exc:
        logger.error(f"Failed to update federation {federation_id}: {exc}")
        _raise_federation_value_error(exc)
    except Exception as exc:
        _raise_sync_error(exc)
    return await _to_detail_response(federation, federation_crud_service, permissions)


def _require_syncable_federation(federation, *, dry_run: bool) -> None:
    if federation.status != FederationStatus.ACTIVE:
        _raise_conflict(f"Federation in status '{federation.status}' cannot be synced")
    if dry_run:
        return
    if not FederationStateMachine.can_start_sync(federation.syncStatus):
        _raise_conflict(f"Federation in sync status '{federation.syncStatus}' cannot start a new sync")


def _validate_sync_provider_config(federation_crud_service, provider_type, provider_config: dict) -> dict:
    try:
        return federation_crud_service.validate_provider_config(provider_type, provider_config)
    except ValueError as exc:
        _raise_federation_value_error(exc)


@router.post("/{federation_id}/sync", response_model=FederationSyncJobResponse | FederationSyncDryRunResponse)
@track_registry_operation("sync", resource_type="federation")
async def sync_federation(
    federation_id: str,
    data: FederationSyncRequest,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    federation_sync_service=Depends(get_federation_sync_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """
        sync a federation.

    main logic:
        1. Validate Request
        2. Create Sync Job
            Create FederationSyncJob
            Set:
                jobType = full_sync
                status = pending
                Update Federation:
                syncStatus = pending
        3. Dispatch by Provider
            Route based on:
            federation.providerType
                AWS → AwsAgentCoreSyncHandler
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
    federation = await _get_required_federation(federation_id, federation_crud_service)
    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_context.get("user_id")),
        resource_type=FEDERATION_RESOURCE_TYPE,
        resource_id=federation.id,
        required_permission="EDIT",
    )
    _require_syncable_federation(federation, dry_run=data.dryRun)
    normalized_provider_config = _validate_sync_provider_config(
        federation_crud_service,
        federation.providerType,
        federation.providerConfig,
    )
    if dict(getattr(federation, "providerConfig", {}) or {}) != dict(normalized_provider_config or {}):
        federation.providerConfig = normalized_provider_config
    triggered_by = user_context.get("user_id")

    try:
        logger.info(f"sync federation {federation.id}, {federation.providerType}")
        if data.dryRun:
            result = await federation_sync_service.preview_manual_sync(
                federation=federation,
                reason=data.reason,
                triggered_by=triggered_by,
            )
            return _to_dry_run_response(result)
        job = await federation_sync_service.start_manual_sync(
            federation=federation,
            reason=data.reason,
            triggered_by=triggered_by,
        )
        return _to_job_response(job)
    except ValueError as exc:
        _raise_federation_value_error(exc)
    except Exception as exc:
        _raise_sync_error(exc)


@router.delete("/{federation_id}", response_model=FederationDeleteResponse)
@track_registry_operation("delete", resource_type="federation")
@use_transaction
async def delete_federation(
    federation_id: str,
    user_context: CurrentUser,
    federation_crud_service=Depends(get_federation_crud_service),
    federation_sync_service=Depends(get_federation_sync_service),
    acl_service: ACLService = Depends(get_acl_service),
):
    """
    Trigger delete job and remove all attached MCP/A2A resources.

    """
    federation = await _get_required_federation(federation_id, federation_crud_service)
    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_context.get("user_id")),
        resource_type=FEDERATION_RESOURCE_TYPE,
        resource_id=federation.id,
        required_permission="DELETE",
    )
    if not FederationStateMachine.can_delete(federation.status):
        _raise_conflict(f"Federation in status '{federation.status}' cannot be deleted")
    try:
        job = await federation_sync_service.start_delete(
            federation=federation,
            triggered_by=user_context.get("user_id"),
        )
        await acl_service.delete_acl_entries_for_resource(
            resource_type=FEDERATION_RESOURCE_TYPE,
            resource_id=federation.id,
        )
        return _to_delete_response(federation, job)
    except ValueError as exc:
        _raise_federation_value_error(exc)
    except Exception as exc:
        _raise_sync_error(exc)
