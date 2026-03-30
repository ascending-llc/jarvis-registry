"""
ACL Management API Routes V1

RESTful API endpoints for managing ACL permissions using MongoDB.
"""

import logging

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status

from registry_pkgs.database.decorators import use_transaction
from registry_pkgs.models._generated import IAccessRole, PrincipalType
from registry_pkgs.models.enums import PermissionBits

from ...auth.dependencies import CurrentUser
from ...deps import get_acl_service
from ...schemas.acl_schema import (
    GetResourcePermissionsResponse,
    PermissionPrincipalOut,
    RoleOut,
    UpdateResourcePermissionsRequest,
    UpdateResourcePermissionsResponse,
)
from ...services.access_control_service import ACLService
from ...utils.utils import validate_resource_type

logger = logging.getLogger(__name__)
router = APIRouter()


def get_user_context(user_context: CurrentUser):
    """Extract user context from authentication dependency"""
    return user_context


@router.get(
    "/permissions/search-principals",
    response_model=list[PermissionPrincipalOut],
    response_model_by_alias=True,
    summary="Search for principals",
    description="Search for principals by query string. Used for ACL sharing UI.",
)
async def search_principals(
    query: str,
    limit: int | None = None,
    principalTypes: list[str] | None = Query(None, alias="principal_types"),
    acl_service: ACLService = Depends(get_acl_service),
) -> list[PermissionPrincipalOut]:
    """
    Search for principals (users, groups, public) matching the query string.
    Returns a paginated response with metadata.
    """
    try:
        response = await acl_service.search_principals(query=query, limit=limit, principal_types=principalTypes)
        return response
    except Exception as e:
        logger.error(f"Error searching principals: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_server_error", "message": "An error occurred while searching principals."},
        )


@router.put(
    "/permissions/{resource_type}/{resource_id}",
    response_model=UpdateResourcePermissionsResponse,
    response_model_by_alias=True,
    summary="Update ACL permissions for a specific resource",
    description="Update ACL permissions for a specific resource",
)
@use_transaction
async def update_resource_permissions(
    resource_id: str,
    resource_type: str,
    data: UpdateResourcePermissionsRequest,
    user_context: dict = Depends(get_user_context),
    acl_service: ACLService = Depends(get_acl_service),
) -> UpdateResourcePermissionsResponse:
    validate_resource_type(resource_type)

    user_id = user_context.get("user_id")
    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_id),
        resource_type=resource_type,
        resource_id=PydanticObjectId(resource_id),
        required_permission="SHARE",
    )

    try:
        await acl_service.validate_at_least_one_owner_remains(
            resource_type=resource_type,
            resource_id=PydanticObjectId(resource_id),
            updated_principals=data.updated,
            removed_principals=data.removed,
        )

        deleted_count = 0
        updated_count = 0
        if data.public:
            # Delete all the ACL entries granting VIEW access
            deleted_count = await acl_service.delete_acl_entries_for_resource(
                resource_type=resource_type,
                resource_id=PydanticObjectId(resource_id),
                perm_bits_to_delete=PermissionBits.VIEW,
            )
            logger.info(f"Deleted {deleted_count} VIEW ACL entries for resource {resource_id}")

            # Create 1 public acl entry
            acl_entry = await acl_service.grant_permission(
                principal_type=PrincipalType.PUBLIC.value,
                principal_id=None,
                resource_type=resource_type,
                resource_id=PydanticObjectId(resource_id),
                perm_bits=PermissionBits.VIEW,
            )
            logger.info(f"Created public ACL entry: {acl_entry.id} for resource {resource_id}")
            updated_count = 1 if acl_entry else 0
        else:
            # Delete the public ACL entry
            deleted_public_entry = await acl_service.delete_permission(
                resource_type=resource_type,
                resource_id=PydanticObjectId(resource_id),
                principal_type=PrincipalType.PUBLIC.value,
                principal_id=None,
            )
            deleted_count += deleted_public_entry
            logger.info(f"Deleted public ACL entry for resource {resource_id}")

        if data.removed:
            for principal in data.removed:
                result = await acl_service.delete_permission(
                    resource_type=resource_type,
                    resource_id=PydanticObjectId(resource_id),
                    principal_type=principal.principalType,
                    principal_id=PydanticObjectId(principal.principalId),
                )
                deleted_count += result

        if data.updated:
            for principal in data.updated:
                role_id = None
                perm_bits = principal.permBits

                if principal.accessRoleId:
                    role = await IAccessRole.find_one({"accessRoleId": principal.accessRoleId})
                    if role:
                        role_id = role.id
                        perm_bits = role.permBits
                    else:
                        logger.warning(f"Role {principal.accessRoleId} not found, using permBits: {perm_bits}")

                await acl_service.grant_permission(
                    principal_type=principal.principalType,
                    principal_id=PydanticObjectId(principal.principalId),
                    resource_type=resource_type,
                    resource_id=PydanticObjectId(resource_id),
                    role_id=role_id,
                    perm_bits=perm_bits,
                )
                updated_count += 1

        logger.info(f"Updated permissions for resource {resource_id}: {updated_count} updated, {deleted_count} deleted")
        return UpdateResourcePermissionsResponse(
            message=f"Updated {updated_count} and deleted {deleted_count} permissions",
            results={"resourceId": resource_id},
        )

    except Exception as e:
        logger.error(f"Error updating permissions for resource {resource_id}: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_server_error", "message": "An error occurred while updating permissions."},
        )


@router.get(
    "/permissions/{resource_type}/roles",
    response_model=list[RoleOut],
    response_model_by_alias=True,
    summary="Get all available roles for a resource type",
    description="Get all available access roles for a specific resource type (e.g., mcpServer, agent).",
)
async def get_resource_type_roles(
    resource_type: str,
    acl_service: ACLService = Depends(get_acl_service),
) -> list[RoleOut]:
    """
    Get all available roles for a specific resource type.
    Returns list of roles with accessRoleId, name, description, and permBits.
    """
    validate_resource_type(resource_type)

    try:
        roles = await acl_service.get_roles_by_resource_type(resource_type=resource_type)
        return roles
    except Exception as e:
        logger.error(f"Error fetching roles for resource type {resource_type}: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "internal_server_error",
                "message": "An error occurred while fetching roles.",
            },
        )


@router.get(
    "/permissions/{resource_type}/{resource_id}",
    response_model=GetResourcePermissionsResponse,
    response_model_by_alias=True,
    summary="Get all permissions for a specific resource",
    description="Get ACL permissions for a specific resource with full principal details.",
)
async def get_resource_permissions(
    resource_type: str,
    resource_id: str,
    user_context: dict = Depends(get_user_context),
    acl_service: ACLService = Depends(get_acl_service),
) -> GetResourcePermissionsResponse:
    """
    Get ACL permissions for a specific resource.
    Returns structured data with principal details (name, email, avatar, etc.) and public status.
    """
    validate_resource_type(resource_type)

    user_id = user_context.get("user_id")
    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_id),
        resource_type=resource_type,
        resource_id=PydanticObjectId(resource_id),
        required_permission="VIEW",
    )

    try:
        result = await acl_service.get_resource_permissions(
            resource_type=resource_type,
            resource_id=PydanticObjectId(resource_id),
        )
        return GetResourcePermissionsResponse(**result)
    except Exception as e:
        logger.error(f"Error fetching resource permissions for {resource_type} {resource_id}: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "internal_server_error",
                "message": "An error occurred while fetching resource permissions.",
            },
        )
