import logging
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException
from fastapi import status as http_status
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.models import (
    Group,
    PrincipalType,
    RegistryAccessRole,
    User,
)
from registry_pkgs.models.enums import PermissionBits
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry

from ..schemas.acl_schema import PermissionPrincipalOut, PrincipalDetailOut, ResourcePermissions, RoleOut
from .group_service import GroupService
from .user_service import UserService

logger = logging.getLogger(__name__)

_REGISTRY_ROLE_RESOURCE_TYPES = [rt.value for rt in RegistryResourceType]


async def load_role_cache() -> dict[tuple[str, int], PydanticObjectId]:
    """Load the static ACL role catalog into an in-memory map keyed by (resourceType, permBits)."""
    cache: dict[tuple[str, int], PydanticObjectId] = {}
    try:
        roles = await RegistryAccessRole.find({"resourceType": {"$in": _REGISTRY_ROLE_RESOURCE_TYPES}}).to_list()
        for role in roles:
            key = (str(role.resourceType), role.permBits)
            if key in cache:
                logger.error(
                    "Duplicate ACL role for %s: roles %s and %s share resourceType+permBits. "
                    "Keeping %s, skipping %s. The (resourceType, permBits) -> roleId mapping must be unique.",
                    key,
                    cache[key],
                    role.id,
                    cache[key],
                    role.id,
                )
                continue
            cache[key] = role.id
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to load ACL role catalog: %s", e)
    return cache


class ACLService:
    def __init__(
        self,
        user_service: UserService,
        group_service: GroupService,
        role_cache: dict[tuple[str, int], PydanticObjectId],
    ):
        self.user_service = user_service
        self.group_service = group_service
        self._role_cache = role_cache

    @property
    def _role_id_to_key(self) -> dict[PydanticObjectId, tuple[str, int]]:
        """Reverse lookup roleId -> (resourceType, permBits), derived live from the cache.

        Built on access rather than at __init__ so it reflects the cache after the
        container populates it at startup. The catalog is tiny (<~20 entries), so
        rebuilding per call is negligible.
        """
        return {rid: key for key, rid in self._role_cache.items()}

    def resolve_perm_bits_for_role(self, resource_type: str, role_id: PydanticObjectId) -> int | None:
        """Resolve perm_bits for a role ObjectId, but only if the role belongs to
        ``resource_type`` (the contract: an ACL entry's roleId must match its own
        resourceType). Returns None for an unknown role or a cross-resource-type
        roleId, so callers reject it rather than silently accepting it. No DB query.
        """
        key = self._role_id_to_key.get(role_id)
        if key is None or key[0] != resource_type:
            return None
        return key[1]

    async def _upsert_acl_entry(
        self,
        *,
        principal_type: str,
        principal_id: PydanticObjectId | str | None,
        resource_type: str,
        resource_id: PydanticObjectId,
        role_id: PydanticObjectId | None,
        perm_bits: int,
        session: AsyncClientSession | None = None,
    ) -> RegistryAclEntry:
        acl_entry = await RegistryAclEntry.find_one(
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

        new_entry = RegistryAclEntry(
            principalType=PrincipalType(principal_type),
            principalId=principal_id,
            resourceType=RegistryResourceType(resource_type),
            resourceId=resource_id,
            roleId=role_id,
            permBits=perm_bits,
            grantedAt=now,
            createdAt=now,
            updatedAt=now,
        )
        await new_entry.insert(session=session)
        return new_entry

    def _principal_result_obj(self, principal_type: PrincipalType, obj: Any) -> PermissionPrincipalOut:
        """
        Helper to construct the PermissionPrincipalOut for users and groups.
        """
        return PermissionPrincipalOut(
            principalType=principal_type,
            principalId=str(obj.id),
            name=getattr(obj, "name", None),
            email=getattr(obj, "email", None),
        )

    def _build_acl_principal_or_clause(
        self,
        user_id: PydanticObjectId,
        group_ids: list[PydanticObjectId],
    ) -> list[dict[str, Any]]:
        """Build the principal match clause used by ACL read paths."""
        or_clause: list[dict[str, Any]] = [
            {"principalType": PrincipalType.USER.value, "principalId": user_id},
            {"principalType": PrincipalType.PUBLIC.value, "principalId": None},
        ]
        if group_ids:
            or_clause.append({"principalType": PrincipalType.GROUP.value, "principalId": {"$in": group_ids}})
        return or_clause

    async def _resolve_group_ids_for_user(
        self,
        user_id: PydanticObjectId,
        session: AsyncClientSession | None = None,
    ) -> list[PydanticObjectId]:
        """Return MongoDB Group _id values for every group the user belongs to.

        Raises on DB error — callers are responsible for deciding whether to
        propagate or absorb the failure. Returns [] for local users with no
        idOnTheSource.
        """
        try:
            user = await User.get(user_id, session=session)
            if not user or not user.idOnTheSource:
                return []
            groups = await Group.find({"memberIds": user.idOnTheSource}, session=session).to_list()
            return [group.id for group in groups]
        except Exception as e:
            logger.warning("Failed to resolve group IDs for user %s: %s", user_id, e)
            raise

    async def grant_permission(
        self,
        principal_type: str,
        principal_id: PydanticObjectId | str | None,
        resource_type: str,
        resource_id: PydanticObjectId,
        perm_bits: int,
        session: AsyncClientSession | None = None,
    ) -> RegistryAclEntry:
        """
        Grant ACL permission to a principal (user or group) for a specific resource.

        The role catalog is resolved from the in-memory ``role_cache`` keyed by
        ``(resource_type, perm_bits)`` — no DB query. A missing role raises rather
        than silently writing a null ``roleId`` (defense-in-depth: catches a new
        resource type added without its seed data).

        Args:
                principal_type (str): Type of principal ('user', 'group', etc.).
                principal_id (Any): ID of the principal (user ID, group ID, etc.).
                resource_type (str): Type of resource (see ResourceType enum).
                resource_id (PydanticObjectId): Resource document ID.
                perm_bits (int): Permission bits to assign.

        Returns:
                RegistryAclEntry: The upserted or newly created ACL entry.

        Raises:
                ValueError: If required parameters are missing/invalid, if a PUBLIC
                        principal is granted anything other than VIEW, or no role matches.
        """
        if principal_type in (PrincipalType.USER, PrincipalType.GROUP) and not principal_id:
            raise ValueError("principal_id must be set for user/group principal_type")

        if principal_type == PrincipalType.PUBLIC and perm_bits != PermissionBits.VIEW:
            raise ValueError("PUBLIC principal may only be granted VIEW permission")

        role_id = self._role_cache.get((resource_type, perm_bits))
        if role_id is None:
            raise ValueError(f"No role found for resource_type={resource_type!r}, perm_bits={perm_bits!r}")

        return await self._upsert_acl_entry(
            principal_type=principal_type,
            principal_id=principal_id,
            resource_type=resource_type,
            resource_id=resource_id,
            role_id=role_id,
            perm_bits=perm_bits,
            session=session,
        )

    async def delete_acl_entries_for_resource(
        self,
        resource_type: str,
        resource_id: PydanticObjectId,
        perm_bits_to_delete: int | None = None,
        session: AsyncClientSession | None = None,
    ) -> int:
        """
        Bulk delete ACL entries for a given resource, optionally deleting all entries with permBits less than or equal to the specified value.

        Raises:
            None (returns 0 on error).
        """
        try:
            query = {"resourceType": resource_type, "resourceId": resource_id}

            if perm_bits_to_delete:
                query["permBits"] = {"$lte": perm_bits_to_delete}

            result = await RegistryAclEntry.find(query).delete(session=session)
            if result is None:
                return 0

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
        session: AsyncClientSession | None = None,
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
            query = {
                "resourceType": resource_type,
                "resourceId": resource_id,
                "principalType": principal_type,
                "principalId": principal_id,
            }
            result = await RegistryAclEntry.find(query).delete(session=session)
            if result is None:
                return 0

            return result.deleted_count
        except Exception as e:
            logger.error(f"Error revoking ACL entry for resource {resource_type} with ID {resource_id}: {e}")
            return 0

    async def search_principals(
        self,
        query: str,
        limit: int | None = 30,
        principal_types: list[str] | None = None,
    ) -> list[PermissionPrincipalOut]:
        """
        Search for principals (users, groups, agents) matching the query string.
        """
        effective_limit = limit if limit is not None and limit > 0 else 30
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
        users: list[Any] = []
        groups: list[Any] = []
        if not type_filters or PrincipalType.USER.value in type_filters:
            users = await self.user_service.search_users(query, limit=effective_limit)

        if not type_filters or PrincipalType.GROUP.value in type_filters:
            groups = await self.group_service.search_groups(query, limit=effective_limit)

        for idx in range(max(len(users), len(groups))):
            if idx < len(users):
                results.append(self._principal_result_obj(PrincipalType.USER, users[idx]))
            if idx < len(groups):
                results.append(self._principal_result_obj(PrincipalType.GROUP, groups[idx]))

        return results[:effective_limit]

    async def get_resource_permissions(
        self,
        resource_type: str,
        resource_id: PydanticObjectId,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        """
        Get all ACL permissions for a specific resource with full principal details.
        Returns structured data including principal information and public status.
        """
        try:
            acl_entries = await RegistryAclEntry.find(
                {"resourceType": resource_type, "resourceId": resource_id},
                session=session,
            ).to_list()

            principals: list[PrincipalDetailOut] = []
            is_public = False
            user_principal_ids: list[tuple[PydanticObjectId, PydanticObjectId | None]] = []
            group_principal_ids: list[tuple[PydanticObjectId, PydanticObjectId | None]] = []

            for entry in acl_entries:
                if entry.principalType == PrincipalType.PUBLIC.value:
                    is_public = True
                elif entry.principalType == PrincipalType.USER.value and entry.principalId:
                    user_principal_ids.append((entry.principalId, entry.roleId))
                elif entry.principalType == PrincipalType.GROUP.value and entry.principalId:
                    group_principal_ids.append((entry.principalId, entry.roleId))

            users_by_id = {}
            if user_principal_ids:
                user_ids = [principal_id for principal_id, _ in user_principal_ids]
                fetched_users = await User.find({"_id": {"$in": user_ids}}, session=session).to_list()
                users_by_id = {user.id: user for user in fetched_users}

            groups_by_id = {}
            if group_principal_ids:
                group_ids = [principal_id for principal_id, _ in group_principal_ids]
                fetched_groups = await Group.find({"_id": {"$in": group_ids}}, session=session).to_list()
                groups_by_id = {group.id: group for group in fetched_groups}

            for principal_id, role_id in user_principal_ids:
                user = users_by_id.get(principal_id)
                if not user:
                    continue
                principals.append(
                    PrincipalDetailOut(
                        type="user",
                        id=str(user.id),
                        name=user.name,
                        email=user.email,
                        avatar=getattr(user, "avatar", None),
                        source=getattr(user, "source", None),
                        idOnTheSource=user.idOnTheSource,
                        roleId=role_id,
                    )
                )

            for principal_id, role_id in group_principal_ids:
                group = groups_by_id.get(principal_id)
                if not group:
                    continue
                principals.append(
                    PrincipalDetailOut(
                        type="group",
                        id=str(group.id),
                        name=group.name,
                        email=group.email,
                        avatar=group.avatar,
                        source=group.source,
                        idOnTheSource=group.idOnTheSource,
                        roleId=role_id,
                    )
                )

            return {
                "resourceType": resource_type,
                "resourceId": str(resource_id),
                "principals": [p.model_dump() for p in principals],
                "public": is_public,
            }
        except Exception as e:
            logger.error(f"Error fetching resource permissions for {resource_type} {resource_id}: {e}")
            raise

    async def get_user_permissions_for_resource(
        self,
        user_id: PydanticObjectId,
        resource_type: str,
        resource_id: PydanticObjectId,
        session: AsyncClientSession | None = None,
    ) -> ResourcePermissions:
        """
        Get the resolved permissions for a single user on a single resource.

        Performs one targeted MongoDB query using $or to match both the
        user-specific ACL entry and any PUBLIC entry for the resource.
        User-specific entries take precedence (sorted by permBits descending).
        """
        try:
            group_ids = await self._resolve_group_ids_for_user(user_id, session=session)
            or_clause = self._build_acl_principal_or_clause(user_id, group_ids)

            acl_entries = (
                await RegistryAclEntry.find(
                    {
                        "resourceType": resource_type,
                        "resourceId": resource_id,
                        "$or": or_clause,
                    },
                    session=session,
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

    async def get_user_permissions_for_resources(
        self,
        user_id: PydanticObjectId,
        resource_type: str,
        resource_ids: list[PydanticObjectId],
        session: AsyncClientSession | None = None,
    ) -> dict[PydanticObjectId, ResourcePermissions]:
        """Batch variant of ``get_user_permissions_for_resource``.

        Single ``$in`` MongoDB query covers every requested ``resource_id``,
        eliminating N+1 round-trips when callers (e.g. list endpoints) need
        per-resource permissions for many resources at once.

        Missing resources are returned with an empty ``ResourcePermissions``
        (mirrors the single-resource fallback) so callers can index without
        ``KeyError`` checks.
        """
        result: dict[PydanticObjectId, ResourcePermissions] = {rid: ResourcePermissions() for rid in resource_ids}
        if not resource_ids:
            return result
        try:
            group_ids = await self._resolve_group_ids_for_user(user_id, session=session)
            or_clause = self._build_acl_principal_or_clause(user_id, group_ids)

            acl_entries = (
                await RegistryAclEntry.find(
                    {
                        "resourceType": resource_type,
                        "resourceId": {"$in": resource_ids},
                        "$or": or_clause,
                    },
                    session=session,
                )
                .sort([("permBits", -1)])
                .to_list()
            )
        except Exception as e:
            logger.error(f"Error batch-fetching permissions for user {user_id} on {resource_type}: {e}")
            return result

        # Entries are sorted by permBits desc → the first one we see per
        # resource is the winner (matches single-resource precedence).
        for entry in acl_entries:
            rid = entry.resourceId
            if rid in result and not any((result[rid].VIEW, result[rid].EDIT, result[rid].DELETE, result[rid].SHARE)):
                bits = int(entry.permBits)
                result[rid] = ResourcePermissions(
                    VIEW=bool(bits & PermissionBits.VIEW),
                    EDIT=bool(bits & PermissionBits.EDIT),
                    DELETE=bool(bits & PermissionBits.DELETE),
                    SHARE=bool(bits & PermissionBits.SHARE),
                )
        return result

    async def check_user_permission(
        self,
        user_id: PydanticObjectId,
        resource_type: str,
        resource_id: PydanticObjectId,
        required_permission: str,
        session: AsyncClientSession | None = None,
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
            session=session,
        )
        if not getattr(permissions, required_permission, False):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=f"You do not have {required_permission} permissions for this resource.",
            )
        return permissions

    async def get_accessible_resource_ids(
        self,
        user_id: PydanticObjectId | None,
        resource_type: str,
        session: AsyncClientSession | None = None,
    ) -> list[str]:
        """
        Return the IDs of all resources of a given type that the user can VIEW.

        When user_id is None (anonymous / unauthenticated), only PUBLIC ACL
        entries are matched.

        Args:
                user_id: The user's ID, or None for anonymous/unauthenticated access.
                resource_type: The resource type string (e.g., RegistryResourceType.MCP_SERVER.value).

        Returns:
                Deduplicated list of resource ID strings the user can VIEW.

        Raises:
                RuntimeError: If the underlying ACL lookup fails (e.g. DB outage).
                        Callers must not interpret this as "no accessible resources" —
                        do so would silently return empty results instead of surfacing
                        the failure.
        """
        try:
            if user_id is not None:
                group_ids = await self._resolve_group_ids_for_user(user_id, session=session)
                principal_filter: dict[str, Any] = {"$or": self._build_acl_principal_or_clause(user_id, group_ids)}
            else:
                principal_filter = {
                    "principalType": PrincipalType.PUBLIC.value,
                    "principalId": None,
                }

            acl_entries = await RegistryAclEntry.find(
                {
                    "resourceType": resource_type,
                    **principal_filter,
                },
                session=session,
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
            raise RuntimeError(f"Failed to fetch accessible {resource_type} resources") from e

    async def get_roles_by_resource_type(
        self,
        resource_type: str,
        session: AsyncClientSession | None = None,
    ) -> list[RoleOut]:
        """
        Get all available roles for a specific resource type.

        Args:
            resource_type: The resource type (e.g., "mcpServer", "agent")

        Returns:
            List of roles (roleId, name, description) in ascending permission order
        """
        roles = (
            await RegistryAccessRole.find({"resourceType": resource_type}, session=session)
            .sort([("permBits", 1)])
            .to_list()
        )
        return [
            RoleOut(
                roleId=role.id,
                name=role.name,
                description=role.description or "",
            )
            for role in roles
        ]

    async def validate_at_least_one_owner_remains(
        self,
        resource_type: str,
        resource_id: PydanticObjectId,
        updated_principals: list[Any],
        removed_principals: list[Any],
        session: AsyncClientSession | None = None,
    ) -> None:
        """
        Validate that after the update, at least one owner remains for the resource.

        Raises:
            HTTPException: 409 if the update would result in no owners remaining
        """
        current_acl_entries = await RegistryAclEntry.find(
            {"resourceType": resource_type, "resourceId": resource_id},
            session=session,
        ).to_list()

        owner_perm_bits = PermissionBits.VIEW | PermissionBits.EDIT | PermissionBits.DELETE | PermissionBits.SHARE

        role_id_to_key = self._role_id_to_key

        def _owner_perm_bits_for(role_id: PydanticObjectId | None) -> int | None:
            key = role_id_to_key.get(role_id)
            if key is None or key[0] != resource_type:
                return None
            return key[1]

        remaining_owners = []
        for entry in current_acl_entries:
            if entry.principalType == PrincipalType.PUBLIC.value:
                continue

            principal_key = f"{entry.principalType}_{entry.principalId}"

            is_being_removed = any(f"{r.principalType}_{r.principalId}" == principal_key for r in removed_principals)

            if is_being_removed:
                continue

            updated_principal = next(
                (u for u in updated_principals if f"{u.principalType}_{u.principalId}" == principal_key),
                None,
            )

            if updated_principal:
                new_perm_bits = _owner_perm_bits_for(updated_principal.roleId)

                if new_perm_bits == owner_perm_bits:
                    remaining_owners.append(principal_key)
            elif entry.permBits == owner_perm_bits:
                remaining_owners.append(principal_key)

        current_keys = {f"{e.principalType}_{e.principalId}" for e in current_acl_entries}

        new_owners = [
            u
            for u in updated_principals
            if u.principalType != PrincipalType.PUBLIC.value
            and f"{u.principalType}_{u.principalId}" not in current_keys
        ]

        for new_principal in new_owners:
            perm_bits = _owner_perm_bits_for(new_principal.roleId)
            if perm_bits == owner_perm_bits:
                remaining_owners.append(f"{new_principal.principalType}_{new_principal.principalId}")

        if not remaining_owners:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail={
                    "error": "owner_required",
                    "message": "At least one owner must remain for the resource",
                },
            )
