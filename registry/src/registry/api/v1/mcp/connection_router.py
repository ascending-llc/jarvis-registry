import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from registry.auth.dependencies import CurrentUser
from registry.schemas.common_api_schemas import (
    ConnectionStatusMapResponse,
    ServerConnectionStatusResponse,
)
from registry.schemas.enums import ConnectionState
from registry.services.oauth.connection_status_service import (
    get_servers_connection_status,
    get_single_server_connection_status,
)
from registry.services.oauth.mcp_service import MCPService, get_mcp_service
from registry.services.server_service import server_service_v1

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["connection"])


@router.post("/{server_id}/reinitialize")
async def reinitialize_server(
    server_id: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> JSONResponse:
    """
    Reinitialize MCP server connection

    Process:
    1. Disconnect existing connection
    2. Check OAuth token status and handle refresh if needed
    3. Create new connection if tokens are valid

    Notes: POST /:serverName/reinitialize (TypeScript reference)
    """
    try:
        user_id = current_user.get("user_id")
        logger.info(f"[Reinitialize] User {user_id} reinitializing server: {server_id}")

        # Step 1: Disconnect existing connection
        disconnected = await mcp_service.connection_service.disconnect_user_connection(user_id, server_id)
        if disconnected:
            logger.info(f"[Reinitialize] Disconnected {server_id} for user {user_id}")

        # Step 2: Get server config
        server = await server_service_v1.get_server_by_id(server_id)
        if not server:
            logger.info(f"[Reinitialize] Server {server_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
        if not server.config:
            logger.info(f"[Reinitialize] Server {server_id} config not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server config not found")

        # Step 3: Handle OAuth authentication
        needs_connection, response_data = await mcp_service.oauth_service.handle_reinitialize_auth(
            user_id=user_id, server=server
        )

        # Step 4: Create connection if tokens are valid
        if needs_connection:
            await mcp_service.connection_service.create_user_connection(
                user_id=user_id,
                server_id=server_id,
                initial_state=ConnectionState.CONNECTED,
                details={"reinitialized": True, "has_oauth": True},
            )
            logger.info(f"[Reinitialize] Created connection for {server_id}")
        return JSONResponse(status_code=status.HTTP_200_OK, content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Reinitialize] Unexpected error for {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal server error: {str(e)}"
        )


@DeprecationWarning
@router.get("/connection/status", response_model=ConnectionStatusMapResponse, response_model_by_alias=True)
async def get_all_connection_status(
    current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> ConnectionStatusMapResponse:
    """
    Get connection status for all MCP servers

    Returns comprehensive connection status for all configured servers.

    Notes: GET /connection/status (TypeScript reference)
    """
    try:
        user_id = current_user.get("user_id")
        logger.debug(f"Fetching connection status for all servers (user: {user_id})")

        # Get all active servers
        all_services, _ = await server_service_v1.list_servers(per_page=1000, status="active")
        logger.info(f"Found {len(all_services)} servers")

        connection_status = await get_servers_connection_status(
            user_id=user_id, servers=all_services, mcp_service=mcp_service
        )
        return ConnectionStatusMapResponse(success=True, connectionStatus=connection_status)

    except Exception as e:
        logger.error(f"Failed to get connection status: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get connection status")


@router.get(
    "/connection/status/{server_id}", response_model=ServerConnectionStatusResponse, response_model_by_alias=True
)
async def get_server_connection_status(
    server_id: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> ServerConnectionStatusResponse:
    """
    Get connection status for a specific MCP server by server ID

    Returns detailed connection status including state, OAuth requirement, and details.
    Uses the same logic as /connection/status to ensure consistency.
    """
    try:
        user_id = current_user.get("user_id")
        logger.debug(f"Fetching status for {server_id} (user: {user_id})")

        server = await server_service_v1.get_server_by_id(server_id)
        if not server:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

        server_status = await get_single_server_connection_status(
            user_id=user_id, server_id=server_id, mcp_service=mcp_service
        )
        return ServerConnectionStatusResponse(
            success=True,
            serverName=server.serverName,
            connectionState=server_status["connection_state"],
            requiresOAuth=server_status["requires_oauth"],
            serverId=server_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get connection status for {server_id}"
        )


@DeprecationWarning
@router.get("/{server_name}/auth-values")
async def check_auth_values(
    server_name: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> dict[str, Any]:
    """
    Check which authentication values are set for an MCP server

    Returns boolean flags indicating which auth variables have values set,
    without exposing the actual credential values.

    Notes: GET /:serverName/auth-values (TypeScript reference)

    Security: Only returns boolean flags, never actual credential values
    """
    try:
        user_id = current_user.get("user_id")
        logger.debug(f"Checking auth values for {server_name} (user: {user_id})")

        # Check if OAuth tokens exist
        tokens = await mcp_service.oauth_service.get_tokens(user_id, server_name)

        auth_value_flags = {"oauth_tokens": tokens is not None}

        # TODO: Check custom user vars from config if needed
        # For now, just return OAuth token status
        return {"success": True, "server_name": server_name, "auth_value_flags": auth_value_flags}

    except Exception as e:
        logger.error(f"Failed to check auth values for {server_name}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to check auth values for {server_name}"
        )
