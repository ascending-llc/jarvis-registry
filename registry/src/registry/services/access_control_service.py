import logging
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException
from fastapi import status as http_status

from registry_pkgs.database.decorators import get_current_session
from registry_pkgs.models._generated import (
    IAccessRole,
    PrincipalType,
)
from registry_pkgs.models.enums import PermissionBits
from registry_pkgs.models.extended_acl_entry import ExtendedAclEntry as IAclEntry

from ..schemas.acl_schema import PermissionPrincipalOut, ResourcePermissions
from .group_service import group_service
from .user_service import user_service

logger = logging.getLogger(__name__)


class ACLService:
    async def _upsert_acl_entry(
        self,
        *,
        principal_type: str,
        principal_id: PydanticObjectId | str | None,
        resource_type: str,
        resource_id: PydanticObjectId,
        role_id: PydanticObjectId | None,
        perm_bits: int | None,
        session=None,
    ) -> IAclEntry:
        acl_entry = await IAclEntry.find_one(
            {
                "principalType": principal_type,
                "principalId": principal_id,
                "resourceType": resource_type,
                "resourceId": resource_id,
            },
            session=session,
        )
        now = datetime.now(UTC)
        if acl_entry:
            acl_entry.permBits = perm_bits
            acl_entry.roleId = role_id
            acl_entry.grantedAt = now
            acl_entry.updatedAt = now
            await acl_entry.save(session=session)
            return acl_entry

        new_entry = IAclEntry(
            principalType=principal_type,
            principalId=principal_id,
            resourceType=resource_type,
            resourceId=resource_id,
            permBits=perm_bits,
            grantedAt=now,
            createdAt=now,
            updatedAt=now,
        )
        await new_entry.insert(session=session)
        return new_entry

    def _principal_result_obj(self, principal_type: str, obj: Any) -> PermissionPrincipalOut:
        """
        Helper to construct the PermissionPrincipalOut for users and groups.
        """
        return PermissionPrincipalOut(
            principal_type=principal_type,
            principal_id=str(obj.id),
            name=getattr(obj, "name", None),
            email=getattr(obj, "email", None),
            accessRoleId=str(getattr(obj, "accessRoleId", ""))
            if hasattr(obj, "accessRoleId") and obj.accessRoleId is not None
            else "",
        )

    async def grant_permission(
        self,
        principal_type: str,
        principal_id: PydanticObjectId | str | None,
        resource_type: str,
        resource_id: PydanticObjectId,
        role_id: PydanticObjectId | None = None,
        perm_bits: int | None = None,
    ) -> IAclEntry:
        """
        Grant ACL permission to a principal (user or group) for a specific resource.

        Args:
                principal_type (str): Type of principal ('user', 'group', etc.).
                principal_id (Any): ID of the principal (user ID, group ID, etc.).
                resource_type (str): Type of resource (see ResourceType enum).
                resource_id (PydanticObjectId): Resource document ID.
                role_id (Optional[PydanticObjectId]): Optional role ID to derive permission bits.
                perm_bits (Optional[int]): Permission bits to assign (overrides role if provided).

        Returns:
                IAclEntry: The upserted or newly created ACL entry.

        Raises:
                ValueError: If required parameters are missing or invalid, or if upsert fails.
        """
        if principal_type in ["user", "group"] and not principal_id:
            raise ValueError("principal_id must be set for user/group principal_type")

        if role_id:
            access_role = await IAccessRole.find_one({"_id": role_id})
            if not access_role:
                raise ValueError("Role not found")
            perm_bits = access_role.permBits

        # Check if an ACL entry already exists for this principal/resource
        try:
            session = None
            try:
                session = get_current_session()
            except RuntimeError:
                session = None

            return await self._upsert_acl_entry(
                principal_type=principal_type,
                principal_id=principal_id,
                resource_type=resource_type,
                resource_id=resource_id,
                role_id=role_id,
                perm_bits=perm_bits,
                session=session,
            )
        except Exception as e:
            if "NoSuchTransaction" in str(e) or "txnNumber" in str(e):
                logger.warning("Retrying ACL upsert without transaction session due to transient tx abort: %s", e)
                try:
                    return await self._upsert_acl_entry(
                        principal_type=principal_type,
                        principal_id=principal_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        role_id=role_id,
                        perm_bits=perm_bits,
                        session=None,
                    )
                except Exception as retry_error:
                    logger.error(f"Error upserting ACL entry on retry: {retry_error}")
                    raise ValueError(f"Error upserting ACL permissions: {retry_error}") from retry_error
            logger.error(f"Error upserting ACL entry: {e}")
            raise ValueError(f"Error upserting ACL permissions: {e}")

    async def delete_acl_entries_for_resource(
        self, resource_type: str, resource_id: PydanticObjectId, perm_bits_to_delete: int | None = None
    ) -> int:
        """
        Bulk delete ACL entries for a given resource, optionally deleting all entries with permBits less than or equal to the specified value.

        Raises:
            None (returns 0 on error).
        """
        try:
            session = get_current_session()
            query = {"resourceType": resource_type, "resourceId": resource_id}

            if perm_bits_to_delete:
                query["permBits"] = {"$lte": perm_bits_to_delete}

            result = await IAclEntry.find(query).delete(session=session)
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error deleting ACL entries for {resource_type}/{resource_id}: {e}")
            return 0

    async def delete_permission(
        self,
        resource_type: str,
        resource_id: PydanticObjectId,
        principal_type: str,
        principal_id: PydanticObjectId | str | None,
    ) -> int:
        """
        Remove a single ACL entry for a given resource, principal type, and principal ID.

        Args:
                resource_type (str): Type of resource (see ResourceType enum).
                resource_id (PydanticObjectId): Resource document ID.
                principal_type (str): Type of principal ('user', 'group', etc.).
                principal_id (Any): ID of the principal (user ID, group ID, etc.).

        Returns:
                int: Number of deleted entries (0 or 1).

        Raises:
                None (returns 0 on error).
        """
        try:
            session = get_current_session()
            query = {
                "resourceType": resource_type,
                "resourceId": resource_id,
                "principalType": principal_type,
                "principalId": principal_id,
            }
            result = await IAclEntry.find(query).delete(session=session)
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error revoking ACL entry for resource {resource_type} with ID {resource_id}: {e}")
            return 0

    async def search_principals(
        self,
        query: str,
        limit: int = 30,
        principal_types: list[str] | None = None,
    ) -> list[PermissionPrincipalOut]:
        """
        Search for principals (users, groups, agents) matching the query string.
        """
        query = (query or "").strip()
        if not query or len(query) < 2:
            raise ValueError("Query string must be at least 2 characters long.")

        valid_types = {PrincipalType.USER.value, PrincipalType.GROUP.value}
        type_filters = None
        if principal_types:
            if isinstance(principal_types, str):
                types = [t.strip() for t in principal_types.split(",") if t.strip()]
            else:
                types = [str(t).strip() for t in principal_types if str(t).strip()]
            type_filters = [t for t in types if t in valid_types]
            if not type_filters:
                type_filters = None

        results: list[PermissionPrincipalOut] = []
        if not type_filters or PrincipalType.USER.value in type_filters:
            for user in await user_service.search_users(query):
                results.append(self._principal_result_obj(PrincipalType.USER.value, user))

        if not type_filters or PrincipalType.GROUP.value in type_filters:
            for group in await group_service.search_groups(query):
                results.append(self._principal_result_obj(PrincipalType.GROUP.value, group))
        return results[:limit]

    async def get_resource_permissions(
        self,
        resource_type: str,
        resource_id: PydanticObjectId,
    ) -> dict[str, Any]:
        """
        Get all ACL permissions for a specific resource.
        """
        try:
            acl_entries = await IAclEntry.find({"resourceType": resource_type, "resourceId": resource_id}).to_list()

            return {"permissions": acl_entries}
        except Exception as e:
            logger.error(f"Error fetching resource permissions for {resource_type} {resource_id}: {e}")
            raise

    async def get_user_permissions_for_resource(
        self,
        user_id: PydanticObjectId,
        resource_type: str,
        resource_id: PydanticObjectId,
    ) -> ResourcePermissions:
        """
        Get the resolved permissions for a single user on a single resource.

        Performs one targeted MongoDB query using $or to match both the
        user-specific ACL entry and any PUBLIC entry for the resource.
        User-specific entries take precedence (sorted by permBits descending).
        """
        try:
            acl_entries = (
                await IAclEntry.find(
                    {
                        "resourceType": resource_type,
                        "resourceId": resource_id,
                        "$or": [
                            {"principalType": PrincipalType.USER.value, "principalId": user_id},
                            {"principalType": PrincipalType.PUBLIC.value, "principalId": None},
                        ],
                    }
                )
                .sort([("permBits", -1)])
                .to_list()
            )

            if not acl_entries:
                return ResourcePermissions()

            acl_entry = acl_entries[0]
            return ResourcePermissions(
                VIEW=bool(int(acl_entry.permBits) & PermissionBits.VIEW),
                EDIT=bool(int(acl_entry.permBits) & PermissionBits.EDIT),
                DELETE=bool(int(acl_entry.permBits) & PermissionBits.DELETE),
                SHARE=bool(int(acl_entry.permBits) & PermissionBits.SHARE),
            )
        except Exception as e:
            logger.error(f"Error fetching permissions for user {user_id} on {resource_type}/{resource_id}: {e}")
            return ResourcePermissions()

    async def check_user_permission(
        self,
        user_id: PydanticObjectId,
        resource_type: str,
        resource_id: PydanticObjectId,
        required_permission: str,
    ) -> ResourcePermissions:
        """
        Verify a user holds a specific permission on a resource.

        Resolves permissions via ``get_user_permissions_for_resource`` and raises
        HTTP 403 if the required permission flag is False.

        Args:
                user_id: The user's ID.
                resource_type: The resource type string.
                resource_id: The resource document ID.
                required_permission: One of 'VIEW', 'EDIT', 'DELETE', 'SHARE'.

        Returns:
                The resolved ResourcePermissions on success.

        Raises:
                HTTPException(403): If the user lacks the required permission.
        """
        permissions = await self.get_user_permissions_for_resource(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        if not getattr(permissions, required_permission, False):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=f"You do not have {required_permission} permissions for this resource.",
            )
        return permissions

    async def get_accessible_resource_ids(
        self,
        user_id: PydanticObjectId,
        resource_type: str,
    ) -> list[str]:
        """
        Return the IDs of all resources of a given type that the user can VIEW.

        Performs a single MongoDB query matching user-specific and PUBLIC
        ACL entries, filters by the VIEW bit, and deduplicates results.

        Args:
                user_id: The user's ID.
                resource_type: The resource type string (e.g., ResourceType.MCPSERVER.value).

        Returns:
                Deduplicated list of resource ID strings the user can VIEW.
                Returns an empty list on error.
        """
        try:
            acl_entries = await IAclEntry.find(
                {
                    "resourceType": resource_type,
                    "$or": [
                        {"principalType": PrincipalType.USER.value, "principalId": user_id},
                        {"principalType": PrincipalType.PUBLIC.value, "principalId": None},
                    ],
                }
            ).to_list()

            seen: set[str] = set()
            result: list[str] = []
            for entry in acl_entries:
                if not (int(entry.permBits) & PermissionBits.VIEW):
                    continue
                rid = str(entry.resourceId)
                if rid not in seen:
                    seen.add(rid)
                    result.append(rid)
            return result
        except Exception as e:
            logger.error(f"Error fetching accessible {resource_type} IDs for user {user_id}: {e}")
            return []


# Singleton instance
acl_service = ACLService()
