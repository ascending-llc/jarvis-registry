"""OAuth re-authorization preflight checks for workflow runs."""

import logging

from fastapi import HTTPException
from fastapi import status as http_status

from registry.schemas.errors import ErrorCode, create_error_detail
from registry.schemas.workflow_api_schemas import PendingAuthorization
from registry.services.oauth.oauth_service import MCPOAuthService
from registry_pkgs.models.enums import McpAuthMode
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowDefinition, collect_executor_keys

logger = logging.getLogger(__name__)


async def collect_pending_oauth_authorizations(
    workflow: WorkflowDefinition,
    *,
    user_id: str,
    oauth_service: MCPOAuthService,
) -> list[PendingAuthorization]:
    """Return OAuth MCP servers that the user must re-authorize before a run."""
    executor_keys = collect_executor_keys(workflow.nodes)
    if not executor_keys:
        return []

    servers = await ExtendedMCPServer.find(
        {"serverName": {"$in": list(executor_keys)}},
        {"config.enabled": True},
    ).to_list()

    pending: list[PendingAuthorization] = []
    for server in servers:
        if server.mcp_auth_mode != McpAuthMode.OAUTH:
            continue

        _, auth_url, error = await oauth_service.get_valid_access_token(user_id=user_id, server=server)
        if error:
            logger.warning("OAuth preflight failed for MCP server %s: %s", server.serverName, error)
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=create_error_detail(
                    ErrorCode.INVALID_REQUEST,
                    f"OAuth token error for server {server.serverName!r}: {error}",
                ),
            )
        if not auth_url:
            continue

        flow_id = oauth_service.flow_manager.generate_flow_id(user_id, str(server.id))
        pending.append(
            PendingAuthorization(
                serverId=str(server.id),
                serverName=server.serverName,
                authUrl=auth_url,
                flowId=flow_id,
            )
        )

    return pending
