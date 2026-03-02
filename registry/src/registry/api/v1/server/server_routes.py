"""
Server Management API Routes V1

RESTful API endpoints for managing MCP servers using MongoDB.
This is a complete rewrite independent of the legacy server_routes.py.
"""

import logging
import math
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from registry_pkgs.database.decorators import use_transaction
from registry_pkgs.models._generated import PrincipalType, ResourceType
from registry_pkgs.models.enums import RoleBits

from ....auth.dependencies import CurrentUser
from ....core.mcp_client import perform_health_check
from ....core.telemetry_decorators import track_registry_operation
from ....schemas.enums import ConnectionState
from ....schemas.server_api_schemas import (
    PaginationMetadata,
    ServerConnectionTestRequest,
    ServerConnectionTestResponse,
    ServerCreateRequest,
    ServerCreateResponse,
    ServerDetailResponse,
    ServerHealthResponse,
    ServerListResponse,
    ServerStatsResponse,
    ServerToggleRequest,
    ServerToggleResponse,
    ServerToolsResponse,
    ServerUpdateRequest,
    ServerUpdateResponse,
    convert_to_create_response,
    convert_to_detail,
    convert_to_health_response,
    convert_to_list_item,
    convert_to_toggle_response,
    convert_to_tools_response,
    convert_to_update_response,
)
from ....services.access_control_service import acl_service
from ....services.oauth.connection_status_service import (
    get_servers_connection_status,
    get_single_server_connection_status,
)
from ....services.oauth.mcp_service import get_mcp_service
from ....services.server_service import server_service_v1

logger = logging.getLogger(__name__)

router = APIRouter()


def get_user_context(user_context: CurrentUser):
    """Extract user context from authentication dependency"""
    return user_context


def apply_connection_status_to_server(
    server_item, status: dict[str, Any] | None, fallback_requires_oauth: bool = False
) -> None:
    """
    Apply connection status to a server response object.
    """
    if status:
        server_item.connectionState = status.get("connection_state")
        server_item.requiresOAuth = status.get("requires_oauth", False)
        server_item.error = status.get("error")
    else:
        # Fallback if status not found
        server_item.connectionState = ConnectionState.ERROR.value
        server_item.requiresOAuth = fallback_requires_oauth
        server_item.error = "Connection status not available"


# ==================== Endpoints ====================


@router.get(
    "/servers",
    response_model=ServerListResponse,
    summary="List Servers",
    description="List all servers with filtering, searching, and pagination. Includes connection status for each server.",
)
@track_registry_operation("list", resource_type="server")
async def list_servers(
    query: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
    user_context: dict = Depends(get_user_context),
):
    """
    List servers with optional filtering and pagination.
    Includes connection status (connection_state, requires_oauth, error) for each server.

    Query Parameters:
    - query: Free-text search across server_name, description, tags
    - status: Filter by operational state (active, inactive, error)
    - page: Page number (default: 1, min: 1)
    - per_page: Items per page (default: 20, min: 1, max: 100)
    """
    try:
        # Validate status if provided
        if status and status not in ["active", "inactive", "error"]:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Invalid status. Must be one of: active, inactive, error",
            )

        user_id = user_context.get("user_id")
        accessible_ids = await acl_service.get_accessible_resource_ids(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.MCPSERVER.value,
        )

        servers, total = await server_service_v1.list_servers(
            query=query,
            status=status,
            page=page,
            per_page=per_page,
            user_id=None,
            accessible_server_ids=accessible_ids,
        )

        server_items = []
        for server in servers:
            perms = await acl_service.get_user_permissions_for_resource(
                user_id=PydanticObjectId(user_id),
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=server.id,
            )
            server_items.append(convert_to_list_item(server, acl_permission=perms))

        # Get connection status and enrich server items
        try:
            user_id = user_context.get("user_id")
            mcp_service = await get_mcp_service()

            connection_status = await get_servers_connection_status(
                user_id=user_id, servers=servers, mcp_service=mcp_service
            )
            # Enrich each server item with connection status
            for server_item in server_items:
                status = connection_status.get(str(server_item.id))
                apply_connection_status_to_server(server_item, status, fallback_requires_oauth=False)

        except Exception as e:
            logger.warning(f"Error getting connection status: {e}", exc_info=True)

        # Calculate pagination metadata
        total_pages = math.ceil(total / per_page) if total > 0 else 0

        return ServerListResponse(
            servers=server_items,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                per_page=per_page,
                total_pages=total_pages,
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing servers: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error while listing servers"
        )


@router.get(
    "/servers/stats",
    response_model=ServerStatsResponse,
    summary="Get System Statistics",
    description="Get system-wide statistics (Admin only). Includes server, token, and user metrics using MongoDB aggregation pipelines.",
)
@track_registry_operation("read", resource_type="stats")
async def get_server_stats(
    user_context: dict = Depends(get_user_context),
):
    """
    Get system-wide statistics.

    **Admin Only Endpoint**

    Returns comprehensive statistics about:
    - Total servers and breakdown by scope, status, and transport
    - Total tokens and breakdown by type, expiry status
    - Active users count
    - Total tools across all servers

    Note: This endpoint uses MongoDB aggregation pipelines and is only
    available when using MongoDB storage backend. File-based storage
    will return a simplified version or 501 Not Implemented.

    Authorization is enforced by ScopePermissionMiddleware based on scopes.yml configuration.
    """
    try:
        # Get statistics from service
        stats = await server_service_v1.get_stats()

        return ServerStatsResponse(**stats)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting server statistics: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while getting statistics",
        )


@router.post(
    "/servers/connection",
    response_model=ServerConnectionTestResponse,
    summary="Check MCP Server Connection",
    description="Check connection and handshake to an MCP server URL without creating a server entry. Returns initialization result. No authentication required.",
)
@track_registry_operation("check", resource_type="connection")
async def check_server_connection(
    data: ServerConnectionTestRequest,
):
    """
    Test connection to an MCP server URL.

    This endpoint allows you to verify connectivity and handshake with an MCP server
    before registering it. It performs an MCP initialization request and returns:
    - Connection success/failure
    - Server name and protocol version (if successful)
    - Response time
    - Server capabilities
    - Error details (if failed)

    **Use this before creating a server to validate the URL and configuration.**
    **No authentication required** - this is a public endpoint for testing connectivity.
    """
    try:
        # Validate URL
        if not data.url:
            return ServerConnectionTestResponse(
                success=False,
                message="URL is required",
                serverName=None,
                protocolVersion=None,
                responseTimeMs=None,
                capabilities=None,
                error="URL is required",
            )

        # Perform health check
        is_healthy, status_msg, response_time_ms, init_result = await perform_health_check(
            url=data.url,
            transport=data.transport or "streamable-http",
        )

        if not is_healthy:
            return ServerConnectionTestResponse(
                success=False,
                message=f"Connection failed: {status_msg}",
                serverName=None,
                protocolVersion=None,
                responseTimeMs=response_time_ms,
                capabilities=None,
                error=status_msg,
            )

        # Extract details from init_result
        server_name = None
        protocol_version = None
        capabilities = None

        if init_result:
            if hasattr(init_result, "serverInfo") and hasattr(init_result.serverInfo, "name"):
                server_name = init_result.serverInfo.name
            if hasattr(init_result, "protocolVersion"):
                protocol_version = init_result.protocolVersion
            if hasattr(init_result, "capabilities"):
                capabilities = init_result.capabilities

        if "initialize requires authentication" in status_msg.lower():
            message = "Connected successfully, initialize requires authentication"
        elif "initialize handshake successful" in status_msg.lower():
            message = "Initialize handshake successful"
            if server_name:
                message = f"Initialize handshake successful - {server_name}"
        else:
            message = f"Successfully connected to {server_name or 'server'}"

        return ServerConnectionTestResponse(
            success=True,
            message=message,
            serverName=server_name,
            protocolVersion=protocol_version,
            responseTimeMs=response_time_ms,
            capabilities=capabilities,
            error=None,
        )

    except Exception as e:
        logger.error(f"Error testing connection to {data.url}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while testing connection",
        )


@router.get(
    "/servers/{server_id}",
    response_model=ServerDetailResponse,
    summary="Get Server Details",
    description="Get detailed information about a specific server, including connection status",
)
@track_registry_operation("read", resource_type="server")
async def get_server(
    server_id: str,
    user_context: dict = Depends(get_user_context),
):
    """Get detailed information about a server by ID, including connection status"""
    try:
        user_id = user_context.get("user_id")
        permissions = await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(server_id),
            required_permission="VIEW",
        )

        server = await server_service_v1.get_server_by_id(
            server_id=server_id,
            user_id=None,
        )
        if not server:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Server not found")

        # Convert to response model
        server_detail = convert_to_detail(server, acl_permission=permissions)
        try:
            mcp_service = await get_mcp_service()
            server_status = await get_single_server_connection_status(
                user_id=user_id, server_id=server.id, mcp_service=mcp_service
            )
            apply_connection_status_to_server(server_detail, server_status)

        except Exception as e:
            logger.warning(f"Error getting connection status for {server.serverName}: {e}", exc_info=True)
            # Apply error state with custom error message
            fallback_requires_oauth = server.config.get("requires_oauth", False)
            apply_connection_status_to_server(server_detail, None, fallback_requires_oauth)
            server_detail.error = f"Failed to get connection status: {str(e)}"

        return server_detail

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error while getting server"
        )


@router.post(
    "/servers",
    response_model=ServerCreateResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Register Server",
    description="Register a new MCP server",
)
@track_registry_operation("create", resource_type="server")
@use_transaction
async def create_server(
    data: ServerCreateRequest,
    user_context: dict = Depends(get_user_context),
):
    """Create a new server"""
    try:
        user_id = user_context.get("user_id")

        server = await server_service_v1.create_server(
            data=data,
            user_id=user_id,
        )

        if not server:
            logger.error("Server creation failed without exception")
            raise ValueError("Failed to create server")

        await acl_service.grant_permission(
            principal_type=PrincipalType.USER,
            principal_id=PydanticObjectId(user_id),
            resource_type=ResourceType.MCPSERVER,
            resource_id=server.id,
            perm_bits=RoleBits.OWNER,
        )

        logger.info(f"Granted user {user_id} {RoleBits.OWNER} permissions for server Id {server.id}")
        return convert_to_create_response(server)

    except ValueError as e:
        error_msg = str(e)

        # Check if authentication error
        if "Authentication required" in error_msg or "not found" in error_msg:
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "unauthorized",
                    "message": error_msg,
                },
            )

        # Business logic errors (e.g., duplicate path, duplicate tags)
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating server: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error while creating server"
        )


@router.patch(
    "/servers/{server_id}",
    response_model=ServerUpdateResponse,
    summary="Update Server",
    description="Update server configuration",
)
@track_registry_operation("update", resource_type="server")
@use_transaction
async def update_server(
    server_id: str,
    data: ServerUpdateRequest,
    user_context: dict = Depends(get_user_context),
):
    """Update a server with partial data"""
    try:
        user_id = user_context.get("user_id")
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(server_id),
            required_permission="EDIT",
        )

        server = await server_service_v1.update_server(
            server_id=server_id,
            data=data,
            user_id=user_id,
        )

        return convert_to_update_response(server)

    except ValueError as e:
        error_msg = str(e)

        # Check if server not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "not_found",
                    "message": error_msg,
                },
            )

        # Other validation errors
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error while updating server"
        )


@router.delete(
    "/servers/{server_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    summary="Delete Server",
    description="Delete a server",
)
@track_registry_operation("delete", resource_type="server")
@use_transaction
async def delete_server(
    server_id: str,
    user_context: dict = Depends(get_user_context),
):
    """Delete a server"""
    try:
        user_id = user_context.get("user_id")
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(server_id),
            required_permission="DELETE",
        )

        successful_delete = await server_service_v1.delete_server(
            server_id=server_id,
            user_id=None,
        )

        if successful_delete:
            deleted_count = await acl_service.delete_acl_entries_for_resource(
                resource_type=ResourceType.MCPSERVER,
                resource_id=PydanticObjectId(server_id),
            )
            logger.info(f"Removed {deleted_count} ACL permissions for server Id {server_id}")
            return None  # 204 No Content
        else:
            raise ValueError(f"Failed to delete server {server_id}. Skipping ACL cleanup")

    except ValueError as e:
        error_msg = str(e)

        # Not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "not_found",
                    "message": "Server not found",
                },
            )

        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error while deleting server"
        )


@router.post(
    "/servers/{server_id}/toggle",
    response_model=ServerToggleResponse,
    summary="Toggle Server Status",
    description="Enable or disable a server",
)
@track_registry_operation("update", resource_type="server")
async def toggle_server(
    server_id: str,
    data: ServerToggleRequest,
    user_context: dict = Depends(get_user_context),
):
    """Toggle server enabled/disabled status. When enabling, fetches tools from server."""
    try:
        user_id = user_context.get("user_id")
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(server_id),
            required_permission="EDIT",
        )

        server = await server_service_v1.toggle_server_status(
            server_id=server_id,
            enabled=data.enabled,
            user_id=user_id,
        )

        return convert_to_toggle_response(server, data.enabled)

    except ValueError as e:
        error_msg = str(e)

        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "not_found",
                    "message": "Server not found",
                },
            )

        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error while toggling server"
        )


@router.get(
    "/servers/{server_id}/tools",
    response_model=ServerToolsResponse,
    summary="Get Server Tools",
    description="Get the list of tools provided by a server",
)
@track_registry_operation("read", resource_type="tool")
async def get_server_tools(
    server_id: str,
    user_context: dict = Depends(get_user_context),
):
    """Get server tools"""
    try:
        user_id = user_context.get("user_id")
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(server_id),
            required_permission="VIEW",
        )

        server, tools = await server_service_v1.get_server_tools(
            server_id=server_id,
            user_id=None,
        )

        return convert_to_tools_response(server, tools)

    except ValueError as e:
        error_msg = str(e)

        if "access denied" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "message": error_msg,
                },
            )

        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "not_found",
                    "message": "Server not found",
                },
            )

        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting tools for server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while getting server tools",
        )


@router.post(
    "/servers/{server_id}/refresh",
    response_model=ServerHealthResponse,
    summary="Refresh Server Health",
    description="Refresh server health status and check connectivity",
)
@track_registry_operation("refresh", resource_type="health")
async def refresh_server_health(
    server_id: str,
    user_context: dict = Depends(get_user_context),
):
    """Refresh server health status. Updates tools if server becomes active."""
    try:
        # Get user_id from context for OAuth token retrieval
        user_id = user_context.get("user_id")

        health_info = await server_service_v1.refresh_server_health(
            server_id=server_id,
            user_id=user_id,
        )

        # Check if health check failed
        if health_info["status"] == "unhealthy":
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "health_check_failed",
                    "message": health_info.get("status_message", "Failed to retrieve tools from server"),
                },
            )

        server = health_info["server"]

        return convert_to_health_response(server, health_info)

    except ValueError as e:
        error_msg = str(e)

        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "not_found",
                    "message": "Server not found",
                },
            )

        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refreshing health for server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while refreshing server health",
        )
