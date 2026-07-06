"""
ACL Management API Routes V1

RESTful API endpoints for managing ACL permissions using MongoDB.
"""

import logging
from dataclasses import dataclass

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import PrincipalType
from registry_pkgs.models.enums import PermissionBits

from ...auth.dependencies import CurrentUser
from ...core.config import settings
from ...deps import get_acl_service, get_group_service
from ...schemas.acl_schema import (
    GetResourcePermissionsResponse,
    PermissionPrincipalOut,
    RoleOut,
    UpdateResourcePermissionsRequest,
    UpdateResourcePermissionsResponse,
)
from ...services.access_control_service import ACLService
from ...services.group_service import GroupService
from ...utils.utils import validate_resource_type

logger = logging.getLogger(__name__)
router = APIRouter()


@dataclass(frozen=True)
class _ResourceContext:
    resource_type: str
    resource_id: str
    user_id: str


def get_user_context(user_context: CurrentUser):
    """Extract user context from authentication dependency"""
    return user_context


def _validate_and_resolve_role_bits(
    acl_service: ACLService,
    resource_type: str,
    data: UpdateResourcePermissionsRequest,
) -> dict[PydanticObjectId, int]:
    """Validate updated principals and resolve each roleId to its permission bitmask.

    Raises HTTPException(400) for PUBLIC principals in `updated` or unknown roleIds.
    """
    perm_bits_by_role: dict[PydanticObjectId, int] = {}
    for principal in data.updated:
        if principal.principalType == PrincipalType.PUBLIC:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_principal_type",
                    "message": (
                        "Public access is managed via the 'public' field. Remove the public principal from 'updated'."
                    ),
                },
            )
        perm_bits = acl_service.resolve_perm_bits_for_role(resource_type, principal.roleId)
        if perm_bits is None:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_role",
                    "message": f"Role {principal.roleId} not found for resource type {resource_type!r}",
                },
            )
        perm_bits_by_role[principal.roleId] = perm_bits
    return perm_bits_by_role


async def _snapshot_entra_groups(
    group_service: GroupService,
    data: UpdateResourcePermissionsRequest,
) -> None:
    """Snapshot Entra group members for every GROUP principal in `updated`.

    Must run before the transaction to avoid holding a lock during paginated Graph API calls.
    """
    for principal in data.updated:
        if principal.principalType == PrincipalType.GROUP:
            await group_service.ensure_group_principal_exists(
                str(principal.principalId),
                enabled=settings.entra_group_sync_enabled,
            )


async def _apply_permissions_in_transaction(
    acl_service: ACLService,
    ctx: _ResourceContext,
    data: UpdateResourcePermissionsRequest,
    perm_bits_by_role: dict[PydanticObjectId, int],
) -> UpdateResourcePermissionsResponse:
    """Open a MongoDB transaction, re-check auth, then apply all ACL changes atomically."""
    async with MongoDB.get_client().start_session() as mongo_session:
        async with await mongo_session.start_transaction():
            await acl_service.check_user_permission(
                user_id=PydanticObjectId(ctx.user_id),
                resource_type=ctx.resource_type,
                resource_id=PydanticObjectId(ctx.resource_id),
                required_permission="SHARE",
                session=mongo_session,
            )
            await acl_service.validate_at_least_one_owner_remains(
                resource_type=ctx.resource_type,
                resource_id=PydanticObjectId(ctx.resource_id),
                updated_principals=data.updated,
                removed_principals=data.removed,
                session=mongo_session,
            )

            deleted_count = 0
            updated_count = 0

            if data.public:
                deleted_count = await acl_service.delete_acl_entries_for_resource(
                    resource_type=ctx.resource_type,
                    resource_id=PydanticObjectId(ctx.resource_id),
                    perm_bits_to_delete=PermissionBits.VIEW,
                    session=mongo_session,
                )
                logger.info(f"Deleted {deleted_count} VIEW ACL entries for resource {ctx.resource_id}")
                acl_entry = await acl_service.grant_permission(
                    principal_type=PrincipalType.PUBLIC.value,
                    principal_id=None,
                    resource_type=ctx.resource_type,
                    resource_id=PydanticObjectId(ctx.resource_id),
                    perm_bits=PermissionBits.VIEW,
                    session=mongo_session,
                )
                logger.info(f"Created public ACL entry: {acl_entry.id} for resource {ctx.resource_id}")
                updated_count = 1 if acl_entry else 0
            else:
                deleted_public_entry = await acl_service.delete_permission(
                    resource_type=ctx.resource_type,
                    resource_id=PydanticObjectId(ctx.resource_id),
                    principal_type=PrincipalType.PUBLIC.value,
                    principal_id=None,
                    session=mongo_session,
                )
                deleted_count += deleted_public_entry
                logger.info(f"Deleted public ACL entry for resource {ctx.resource_id}")

            for principal in data.removed:
                principal_id = (
                    None if principal.principalType == PrincipalType.PUBLIC else PydanticObjectId(principal.principalId)
                )
                deleted_count += await acl_service.delete_permission(
                    resource_type=ctx.resource_type,
                    resource_id=PydanticObjectId(ctx.resource_id),
                    principal_type=principal.principalType,
                    principal_id=principal_id,
                    session=mongo_session,
                )

            for principal in data.updated:
                principal_id = (
                    None if principal.principalType == PrincipalType.PUBLIC else PydanticObjectId(principal.principalId)
                )
                await acl_service.grant_permission(
                    principal_type=principal.principalType,
                    principal_id=principal_id,
                    resource_type=ctx.resource_type,
                    resource_id=PydanticObjectId(ctx.resource_id),
                    perm_bits=perm_bits_by_role[principal.roleId],
                    session=mongo_session,
                )
                updated_count += 1

            logger.info(
                f"Updated permissions for resource {ctx.resource_id}: {updated_count} updated, {deleted_count} deleted"
            )
            return UpdateResourcePermissionsResponse(
                message=f"Updated {updated_count} and deleted {deleted_count} permissions",
                results={"resourceId": ctx.resource_id},
            )


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
async def update_resource_permissions(
    resource_id: str,
    resource_type: str,
    data: UpdateResourcePermissionsRequest,
    user_context: dict = Depends(get_user_context),
    acl_service: ACLService = Depends(get_acl_service),
    group_service: GroupService = Depends(get_group_service),
) -> UpdateResourcePermissionsResponse:
    validate_resource_type(resource_type)
    ctx = _ResourceContext(
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_context.get("user_id"),
    )
    try:
        await acl_service.check_user_permission(
            user_id=PydanticObjectId(ctx.user_id),
            resource_type=ctx.resource_type,
            resource_id=PydanticObjectId(ctx.resource_id),
            required_permission="SHARE",
        )
        perm_bits_by_role = _validate_and_resolve_role_bits(acl_service, ctx.resource_type, data)
        await _snapshot_entra_groups(group_service, data)
        return await _apply_permissions_in_transaction(acl_service, ctx, data, perm_bits_by_role)
    except HTTPException:
        raise
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
    Returns list of roles with roleId, name, and description in ascending permission order.
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
