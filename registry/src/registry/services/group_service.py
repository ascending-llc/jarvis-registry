"""Group management service: search and Entra group membership sync."""

import logging

from beanie import PydanticObjectId

from registry_pkgs.models._generated.group import Group, GroupSource
from registry_pkgs.models._generated.user import User

from .group_directory_client import IdPGroupDirectoryClient

logger = logging.getLogger(__name__)


class GroupService:
    def __init__(self, group_directory_client: IdPGroupDirectoryClient) -> None:
        self._directory_client = group_directory_client

    async def search_groups(self, query: str, limit: int = 30) -> list[Group]:
        """Search groups by name or email (case-insensitive substring match)."""
        search_query = {
            "$or": [
                {"name": {"$regex": query, "$options": "i"}},
                {"email": {"$regex": query, "$options": "i"}},
            ]
        }
        try:
            return await Group.find(search_query).limit(limit).to_list()
        except Exception as e:
            logger.error("Error searching groups with query '%s': %s", query, e)
            return []

    async def sync_user_group_memberships(self, user: User, *, enabled: bool) -> None:
        """Sync Entra group membership for the given user into MongoDB Group documents.

        Port of PermissionService.syncUserEntraGroupMemberships from jarvis-api.
        Non-destructive when Graph API returns an empty list (protects against transient failures).
        """
        if not enabled:
            return
        if not user.idOnTheSource:
            return

        user_oid: str = user.idOnTheSource
        group_ids = await self._directory_client.get_user_group_ids(user_oid)
        if not group_ids:
            return

        # Add user to existing matching Entra groups
        await Group.find(
            {
                "idOnTheSource": {"$in": group_ids},
                "source": GroupSource.ENTRA,
                "memberIds": {"$ne": user_oid},
            }
        ).update_many({"$addToSet": {"memberIds": user_oid}})

        # Find which groups don't exist in DB yet
        existing = await Group.find({"idOnTheSource": {"$in": group_ids}, "source": GroupSource.ENTRA}).to_list()
        existing_source_ids = {g.idOnTheSource for g in existing}
        missing_ids = [gid for gid in group_ids if gid not in existing_source_ids]

        # Upsert missing groups, then add user to them
        if missing_ids:
            details = await self._directory_client.get_group_details_batch(missing_ids)
            if len(details) < len(missing_ids):
                logger.warning(
                    "get_group_details_batch resolved %d/%d groups; remaining will retry on next login.",
                    len(details),
                    len(missing_ids),
                )
            detail_ids: list[str] = []
            for detail in details:
                detail_ids.append(detail["id"])
                await Group.find({"idOnTheSource": detail["id"], "source": GroupSource.ENTRA}).update_many(
                    {
                        "$setOnInsert": {
                            "name": detail["name"],
                            "email": detail.get("email"),
                            "description": detail.get("description"),
                            "source": GroupSource.ENTRA,
                            "idOnTheSource": detail["id"],
                            "memberIds": [],
                        }
                    },
                    upsert=True,
                )
            if detail_ids:
                await Group.find({"idOnTheSource": {"$in": detail_ids}, "source": GroupSource.ENTRA}).update_many(
                    {"$addToSet": {"memberIds": user_oid}}
                )

        # Stale removal: remove user from Entra groups they no longer belong to
        await Group.find(
            {
                "source": GroupSource.ENTRA,
                "memberIds": user_oid,
                "idOnTheSource": {"$nin": group_ids},
            }
        ).update_many({"$pullAll": {"memberIds": [user_oid]}})

    async def ensure_group_principal_exists(self, group_id: str, *, enabled: bool) -> None:
        """Snapshot all transitive Entra group members into Group.memberIds before ACL grant.

        Port of PermissionService.ensureGroupPrincipalExists from jarvis-api (Entra branch only).
        Errors from the directory client are re-raised so the ACL grant route returns 500.
        """
        if not enabled:
            return

        group = await Group.get(PydanticObjectId(group_id))
        if group is None:
            return
        if group.source != GroupSource.ENTRA:
            return
        if not group.idOnTheSource:
            return

        try:
            member_oids = await self._directory_client.get_group_members(group.idOnTheSource)
        except Exception:
            logger.error(
                "Failed to fetch group members for group %s (idOnTheSource=%s); ACL grant aborted.",
                group_id,
                group.idOnTheSource,
                exc_info=True,
            )
            raise
        await group.set({"memberIds": member_oids})
