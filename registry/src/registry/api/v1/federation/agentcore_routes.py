from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from ....auth.dependencies import CurrentUser
from ....core.telemetry_decorators import track_registry_operation
from ....schemas.errors import ErrorCode, create_error_detail
from ....services.agentcore_import_service import get_agentcore_import_service

router = APIRouter()


class AgentCoreRuntimeSyncRequest(BaseModel):
    dryRun: bool = Field(default=False, description="Preview only, no persistence")


class AgentCoreSyncCounter(BaseModel):
    mcp_servers: int
    a2a_agents: int


class AgentCoreSyncDiscoveredCounter(AgentCoreSyncCounter):
    skipped_runtimes: int


class AgentCoreSyncEntityResult(BaseModel):
    action: str
    changes: list[str] = Field(default_factory=list)
    error: str | None = None
    server_name: str | None = None
    server_id: str | None = None
    agent_name: str | None = None
    agent_id: str | None = None


class AgentCoreRuntimeSyncResponse(BaseModel):
    runtime_filter_count: int
    discovered: AgentCoreSyncDiscoveredCounter
    created: AgentCoreSyncCounter
    updated: AgentCoreSyncCounter
    deleted: AgentCoreSyncCounter
    skipped: AgentCoreSyncCounter
    errors: list[str] = Field(default_factory=list)
    mcp_servers: list[AgentCoreSyncEntityResult] = Field(default_factory=list)
    a2a_agents: list[AgentCoreSyncEntityResult] = Field(default_factory=list)
    skipped_runtimes: list[dict[str, Any]] = Field(default_factory=list)
    duration_seconds: float


@router.post(
    "/federation/agentcore/runtime/sync",
    response_model=AgentCoreRuntimeSyncResponse,
    summary="Manual AgentCore Runtime Sync",
    description="Manual synchronous sync from AgentCore runtimes (MCP + A2A).",
)
@track_registry_operation("sync", resource_type="federation")
async def sync_agentcore_runtime(
    data: AgentCoreRuntimeSyncRequest,
    user_context: CurrentUser,
) -> AgentCoreRuntimeSyncResponse:
    """
    Manual runtime sync entrypoint.
    No background jobs; request blocks until import completes.
    """
    try:
        result = await get_agentcore_import_service().import_from_runtime(
            dry_run=data.dryRun,
            user_id=user_context.get("user_id"),
        )
        return AgentCoreRuntimeSyncResponse(**result)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, str(exc)),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, f"AgentCore runtime sync failed: {exc}"),
        ) from exc
