"""
A2A Agent Management API Routes V1

RESTful API endpoints for managing A2A agents using MongoDB.
Includes well-known agent card discovery endpoint.
"""

import logging
import math
from typing import Annotated, Literal

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from fastapi.responses import JSONResponse

from registry.auth.dependencies import CurrentUser
from registry.core.exceptions import (
    A2AAgentCardNotFoundException,
    A2AAgentCardParseException,
    A2AAgentCardTransportException,
    A2AAgentCardUpstreamException,
)
from registry.core.telemetry_decorators import track_registry_operation
from registry.deps import get_a2a_agent_service, get_acl_service
from registry.schemas.a2a_agent_api_schemas import (
    AgentCreateRequest,
    AgentDetailResponse,
    AgentListResponse,
    AgentSkillsResponse,
    AgentStatsResponse,
    AgentToggleRequest,
    AgentUpdateRequest,
    PaginationMetadata,
    WellKnownSyncResponse,
    convert_to_detail,
    convert_to_list_item,
    convert_to_skills_response,
)
from registry_pkgs.database.decorators import use_transaction
from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import RoleBits

from ....schemas.acl_schema import ResourcePermissions
from ....schemas.errors import ErrorCode, create_error_detail
from ....services.a2a_agent_service import A2AAgentService
from ....services.access_control_service import ACLService

logger = logging.getLogger(__name__)

router = APIRouter()


def check_admin_permission(user_context: dict) -> bool:
    """
    Check if user has admin permissions.

    Args:
        user_context: User context dictionary from authentication

    Returns:
        True if user has admin scope, False otherwise
    """
    scopes = user_context.get("scopes", [])
    return "mcp-registry-admin" in scopes


# ==================== Endpoints ====================


@router.get(
    "/agents",
    response_model=AgentListResponse,
    response_model_by_alias=True,  # Use camelCase in API responses
    summary="List Agents",
    description="List all agents with filtering, searching, and pagination",
)
@track_registry_operation("list", resource_type="agent")
async def list_agents(
    user_context: CurrentUser,
    query: str | None = None,
    status: Annotated[Literal["active", "inactive", "error"] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """
    List agents with optional filtering and pagination.

    Query Parameters:
    - query: Free-text search across agent name, description, tags, skills
    - status: Filter by operational state (active, inactive, error)
    - page: Page number (default: 1, min: 1)
    - per_page: Items per page (default: 20, min: 1, max: 100)
    """
    try:
        # Get accessible agent IDs from ACL
        user_id = user_context.get("user_id")
        accessible_ids = await acl_service.get_accessible_resource_ids(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT.value,
        )

        # List agents
        agents, total = await a2a_agent_service.list_agents(
            query=query,
            status=status,
            page=page,
            per_page=per_page,
            accessible_agent_ids=accessible_ids,
        )

        # Convert to response items with permissions
        agent_items = []
        for agent in agents:
            perms = await acl_service.get_user_permissions_for_resource(
                user_id=PydanticObjectId(user_id),
                resource_type=ResourceType.REMOTE_AGENT.value,
                resource_id=agent.id,
            )
            agent_items.append(convert_to_list_item(agent, acl_permission=perms))

        # Calculate pagination metadata
        total_pages = math.ceil(total / per_page) if total > 0 else 0

        return AgentListResponse(
            agents=agent_items,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                perPage=per_page,
                totalPages=total_pages,
            ),
        )

    except HTTPException:
        logger.exception("HTTPException in list_agents")
        raise
    except Exception as e:
        logger.error(f"Error listing agents: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while listing agents"),
        )


@router.get(
    "/agents/stats",
    response_model=AgentStatsResponse,
    summary="Get Agent Statistics",
    description="Get system-wide agent statistics (Admin only)",
)
@track_registry_operation("read", resource_type="stats")
async def get_agent_stats(
    user_context: CurrentUser,
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """
    Get system-wide agent statistics.

    **Admin Only Endpoint**

    Returns comprehensive statistics about:
    - Total agents and breakdown by enabled/disabled
    - Breakdown by status (active, inactive, error)
    - Breakdown by transport type
    - Total skills and average skills per agent
    """
    try:
        if not check_admin_permission(user_context):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=create_error_detail(
                    ErrorCode.INSUFFICIENT_PERMISSIONS, "Admin access required to view agent statistics"
                ),
            )

        # Get statistics from service
        stats = await a2a_agent_service.get_stats()

        return AgentStatsResponse(
            totalAgents=stats["total_agents"],
            enabledAgents=stats["enabled_agents"],
            disabledAgents=stats["disabled_agents"],
            byStatus=stats["by_status"],
            byTransport=stats["by_transport"],
            totalSkills=stats["total_skills"],
            averageSkillsPerAgent=stats["average_skills_per_agent"],
        )

    except HTTPException:
        logger.exception("HTTPException in get_agent_stats")
        raise
    except Exception as e:
        logger.error(f"Error getting agent statistics: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while getting statistics"),
        )


@router.get(
    "/agents/{agent_id}",
    response_model=AgentDetailResponse,
    response_model_by_alias=True,  # Use camelCase in API responses
    summary="Get Agent Detail",
    description="Get detailed information about a specific agent",
)
@track_registry_operation("read", resource_type="agent")
async def get_agent(
    agent_id: str,
    user_context: CurrentUser,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """Get detailed information about an agent by ID"""
    try:
        user_id = user_context.get("user_id")

        # Check VIEW permission
        permissions = await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT.value,
            resource_id=PydanticObjectId(agent_id),
            required_permission="VIEW",
        )

        # Get agent
        agent = await a2a_agent_service.get_agent_by_id(agent_id)

        # Convert to response model
        return convert_to_detail(agent, acl_permission=permissions)

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )
    except HTTPException:
        logger.exception("HTTPException in get_agent")
        raise
    except Exception as e:
        logger.error(f"Error getting agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while getting agent"),
        )


@router.post(
    "/agents",
    response_model=AgentDetailResponse,
    response_model_by_alias=True,  # Use camelCase in API responses
    status_code=http_status.HTTP_201_CREATED,
    summary="Create Agent",
    description="Register a new A2A agent",
)
@track_registry_operation("create", resource_type="agent")
@use_transaction
async def create_agent(
    data: AgentCreateRequest,
    user_context: CurrentUser,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """Create a new agent"""
    try:
        user_id = user_context.get("user_id")

        # Create agent
        agent = await a2a_agent_service.create_agent(data=data, user_id=user_id)

        if not agent:
            logger.error("Agent creation failed without exception")
            raise ValueError("Failed to create agent")

        # Grant OWNER permission to creator
        await acl_service.grant_permission(
            principal_type=PrincipalType.USER,
            principal_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT,
            resource_id=agent.id,
            perm_bits=RoleBits.OWNER,
        )

        logger.info(f"Granted user {user_id} OWNER permissions for agent {agent.id}")

        permissions = ResourcePermissions(
            VIEW=True,
            EDIT=True,
            DELETE=True,
            SHARE=True,
        )

        return convert_to_detail(agent, acl_permission=permissions)

    except ValueError as e:
        error_msg = str(e)

        # Check if duplicate path
        if "already exists" in error_msg:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=create_error_detail(ErrorCode.DUPLICATE_ENTRY, error_msg),
            )

        # Other validation errors
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in create_agent")
        raise
    except Exception as e:
        logger.error(f"Error creating agent: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while creating agent"),
        )


@router.patch(
    "/agents/{agent_id}",
    response_model=AgentDetailResponse,
    response_model_by_alias=True,  # Use camelCase in API responses
    summary="Update Agent",
    description="Update agent configuration",
)
@track_registry_operation("update", resource_type="agent")
@use_transaction
async def update_agent(
    agent_id: str,
    data: AgentUpdateRequest,
    user_context: CurrentUser,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """Update an agent with partial data"""
    try:
        user_id = user_context.get("user_id")

        # Check EDIT permission and get permissions for response
        permissions = await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT.value,
            resource_id=PydanticObjectId(agent_id),
            required_permission="EDIT",
        )

        # Update agent
        agent = await a2a_agent_service.update_agent(agent_id=agent_id, data=data)

        return convert_to_detail(agent, acl_permission=permissions)

    except ValueError as e:
        error_msg = str(e)

        # Check if agent not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )

        # Other validation errors
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in update_agent")
        raise
    except Exception as e:
        logger.error(f"Error updating agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while updating agent"),
        )


@router.delete(
    "/agents/{agent_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    summary="Delete Agent",
    description="Delete an agent",
)
@track_registry_operation("delete", resource_type="agent")
@use_transaction
async def delete_agent(
    agent_id: str,
    user_context: CurrentUser,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """Delete an agent"""
    try:
        user_id = user_context.get("user_id")

        # Check DELETE permission
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT.value,
            resource_id=PydanticObjectId(agent_id),
            required_permission="DELETE",
        )

        # Delete agent
        successful_delete = await a2a_agent_service.delete_agent(agent_id=agent_id)

        if successful_delete:
            # Delete all associated ACL permission records
            deleted_count = await acl_service.delete_acl_entries_for_resource(
                resource_type=ResourceType.REMOTE_AGENT,
                resource_id=PydanticObjectId(agent_id),
            )
            logger.info(f"Removed {deleted_count} ACL permissions for agent {agent_id}")
            return None  # 204 No Content
        else:
            raise ValueError(f"Failed to delete agent {agent_id}. Skipping ACL cleanup")

    except ValueError as e:
        error_msg = str(e)

        # Not found
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, "Agent not found"),
            )

        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in delete_agent")
        raise
    except Exception as e:
        logger.error(f"Error deleting agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while deleting agent"),
        )


@router.post(
    "/agents/{agent_id}/toggle",
    response_model=AgentDetailResponse,
    response_model_by_alias=True,  # Use camelCase in API responses
    summary="Toggle Agent Status",
    description="Enable or disable an agent",
)
@track_registry_operation("update", resource_type="agent")
async def toggle_agent(
    agent_id: str,
    data: AgentToggleRequest,
    user_context: CurrentUser,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """Toggle agent enabled/disabled status"""
    try:
        user_id = user_context.get("user_id")

        # Check EDIT permission and get permissions for response
        permissions = await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT.value,
            resource_id=PydanticObjectId(agent_id),
            required_permission="EDIT",
        )

        # Toggle agent status
        agent = await a2a_agent_service.toggle_agent_status(agent_id=agent_id, enabled=data.enabled)

        return convert_to_detail(agent, acl_permission=permissions)

    except ValueError as e:
        error_msg = str(e)

        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, "Agent not found"),
            )

        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )

    except HTTPException:
        logger.exception("HTTPException in toggle_agent")
        raise
    except Exception as e:
        logger.error(f"Error toggling agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while toggling agent"),
        )


@router.get(
    "/agents/{agent_id}/skills",
    response_model=AgentSkillsResponse,
    summary="Get Agent Skills",
    description="Get the list of skills provided by an agent",
)
@track_registry_operation("read", resource_type="skill")
async def get_agent_skills(
    agent_id: str,
    user_context: CurrentUser,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """Get agent skills"""
    try:
        user_id = user_context.get("user_id")

        # Check VIEW permission
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT.value,
            resource_id=PydanticObjectId(agent_id),
            required_permission="VIEW",
        )

        # Get agent
        agent = await a2a_agent_service.get_agent_by_id(agent_id)

        return convert_to_skills_response(agent)

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
        )
    except HTTPException:
        logger.exception("HTTPException in get_agent_skills")
        raise
    except Exception as e:
        logger.error(f"Error getting skills for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while getting agent skills"),
        )


@router.post(
    "/agents/{agent_id}/wellknown",
    response_model=WellKnownSyncResponse,
    summary="Sync Well-Known",
    description="Sync agent configuration from .well-known/agent-card.json endpoint",
)
@track_registry_operation("update", resource_type="agent")
async def sync_wellknown(
    agent_id: str,
    user_context: CurrentUser,
    acl_service: ACLService = Depends(get_acl_service),
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
):
    """Sync agent configuration from well-known endpoint"""
    try:
        user_id = user_context.get("user_id")

        # Check EDIT permission
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(user_id),
            resource_type=ResourceType.REMOTE_AGENT.value,
            resource_id=PydanticObjectId(agent_id),
            required_permission="EDIT",
        )

        # Sync well-known
        result = await a2a_agent_service.sync_wellknown(agent_id=agent_id)

        return WellKnownSyncResponse(
            message=result["message"],
            syncStatus=result["sync_status"],
            syncedAt=result["synced_at"],
            version=result["version"],
            changes=result["changes"],
        )

    except A2AAgentCardNotFoundException as e:
        error_msg = str(e)
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
        )
    except A2AAgentCardTransportException as e:
        error_msg = str(e)
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=create_error_detail(ErrorCode.SERVICE_UNAVAILABLE, error_msg),
        )
    except (A2AAgentCardUpstreamException, A2AAgentCardParseException) as e:
        error_msg = str(e)
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=create_error_detail(ErrorCode.EXTERNAL_SERVICE_ERROR, error_msg),
        )
    except ValueError as e:
        error_msg = str(e)

        # Check error types
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
            )

        if "not enabled" in error_msg.lower() or "not configured" in error_msg.lower():
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=create_error_detail(ErrorCode.INVALID_REQUEST, error_msg),
            )

        # Other errors
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=create_error_detail(ErrorCode.EXTERNAL_SERVICE_ERROR, error_msg),
        )
    except Exception as e:
        logger.error(f"Error syncing well-known for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, "Internal server error while syncing well-known"),
        )


@router.get(
    "/agents/.well-known/agent-cards",
    summary="Get Agent Card (Well-Known)",
    description="Get agent card from well-known endpoint by URL following A2A protocol",
)
async def get_agent_wellknown_card(
    url: Annotated[str, Query(description="The A2A agent URL to fetch card from")],
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
) -> JSONResponse:
    """
    Get agent card in A2A protocol format from well-known endpoint by URL.

    This endpoint is used during agent creation (before ID is assigned) to validate
    and fetch the agent card from the provided URL.

    Endpoint: /api/v1/agents/.well-known/agent-cards?url=XXXX
    """
    try:
        # Validate URL format
        if not url or not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=create_error_detail(ErrorCode.INVALID_REQUEST, "Invalid URL format"),
            )

        # Use service layer method with built-in fallback logic
        agent_card = await a2a_agent_service._fetch_agent_card_from_url(url)

        # Convert agent card to dict
        agent_card_data = agent_card.model_dump(mode="json", exclude_none=True, by_alias=True)

        # Return without cache headers (since this is for creation/validation, not caching)
        headers = {
            "Content-Type": "application/json",
        }

        logger.info(f"Successfully fetched and returned agent card from URL: {url}")
        return JSONResponse(content=agent_card_data, headers=headers)

    except A2AAgentCardNotFoundException as e:
        error_msg = str(e)
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=create_error_detail(ErrorCode.RESOURCE_NOT_FOUND, error_msg),
        )
    except A2AAgentCardTransportException as e:
        error_msg = str(e)
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=create_error_detail(ErrorCode.SERVICE_UNAVAILABLE, error_msg),
        )
    except (A2AAgentCardUpstreamException, A2AAgentCardParseException) as e:
        error_msg = str(e)
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=create_error_detail(ErrorCode.EXTERNAL_SERVICE_ERROR, error_msg),
        )
    except Exception as e:
        logger.error(f"Error getting well-known agent card from URL {url}: {e}", exc_info=True)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(ErrorCode.INTERNAL_ERROR, f"Failed to fetch agent card from {url}: {str(e)}"),
        )
