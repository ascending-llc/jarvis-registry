"""Group management service: search and per-provider IdP group membership sync."""

import logging

from beanie import BulkWriter, PydanticObjectId

from registry_pkgs.models import ExtendedGroup, ExtendedGroupSource
from registry_pkgs.models._generated.user import User

from .group_directory_client import IdPGroupDirectoryClient

logger = logging.getLogger(__name__)


class GroupService:
    def __init__(self, directory_clients: dict[ExtendedGroupSource, IdPGroupDirectoryClient]) -> None:
        self._clients = directory_clients

    async def search_groups(self, query: str, limit: int = 30) -> list[ExtendedGroup]:
        """Search groups by name or email (case-insensitive substring match)."""
        search_query = {
            "$or": [
                {"name": {"$regex": query, "$options": "i"}},
                {"email": {"$regex": query, "$options": "i"}},
            ]
        }
        try:
            return await ExtendedGroup.find(search_query, projection_model=ExtendedGroup).limit(limit).to_list()
        except Exception as e:
            logger.error("Error searching groups with query '%s': %s", query, e)
            return []

    async def sync_user_group_memberships(self, user: User) -> None:
        """Sync IdP group membership for the given user into MongoDB ExtendedGroup documents.

        The directory client and enablement are resolved per-user from ``user.provider``.
        Port of PermissionService.syncUserEntraGroupMemberships from jarvis-api.
        Non-destructive when the directory returns an empty list (protects against transient failures).
        """
        source = ExtendedGroupSource.GOOGLE if user.provider == "google" else ExtendedGroupSource.ENTRA
        client = self._clients.get(source)
        if client is None or not user.idOnTheSource:
            return

        user_oid: str = user.idOnTheSource
        group_ids = await client.get_user_group_ids(user_oid)
        if not group_ids:
            return

        await self._add_user_to_known_groups(source, user_oid, group_ids)
        await self._upsert_new_groups_and_enroll_user(source, client, user_oid, group_ids)
        await self._remove_user_from_stale_groups(source, user_oid, group_ids)

    async def _add_user_to_known_groups(self, source: ExtendedGroupSource, user_oid: str, group_ids: list[str]) -> None:
        """$addToSet the user into groups that already exist in the DB."""
        await ExtendedGroup.find(
            {
                "idOnTheSource": {"$in": group_ids},
                "source": source,
                "memberIds": {"$ne": user_oid},
            }
        ).update_many({"$addToSet": {"memberIds": user_oid}})

    async def _upsert_new_groups_and_enroll_user(
        self, source: ExtendedGroupSource, client: IdPGroupDirectoryClient, user_oid: str, group_ids: list[str]
    ) -> None:
        """Fetch details for groups absent from the DB, upsert them, then enroll the user."""
        existing = await ExtendedGroup.find({"idOnTheSource": {"$in": group_ids}, "source": source}).to_list()
        existing_source_ids = {g.idOnTheSource for g in existing}
        missing_ids = [gid for gid in group_ids if gid not in existing_source_ids]
        if not missing_ids:
            return

        details = await client.get_group_details_batch(missing_ids)
        if len(details) < len(missing_ids):
            logger.warning(
                "get_group_details_batch resolved %d/%d groups; remaining will retry on next login.",
                len(details),
                len(missing_ids),
            )

        detail_ids: list[str] = []
        async with BulkWriter() as bulk_writer:
            for detail in details:
                detail_ids.append(detail["id"])
                await ExtendedGroup.find({"idOnTheSource": detail["id"], "source": source}).update_many(
                    {
                        "$setOnInsert": {
                            "name": detail["name"],
                            "email": detail.get("email"),
                            "description": detail.get("description"),
                            "source": source,
                            "idOnTheSource": detail["id"],
                            "memberIds": [],
                        }
                    },
                    upsert=True,
                    bulk_writer=bulk_writer,
                )

        if detail_ids:
            await ExtendedGroup.find({"idOnTheSource": {"$in": detail_ids}, "source": source}).update_many(
                {"$addToSet": {"memberIds": user_oid}}
            )

    async def _remove_user_from_stale_groups(
        self, source: ExtendedGroupSource, user_oid: str, group_ids: list[str]
    ) -> None:
        """$pullAll the user from groups they no longer belong to."""
        await ExtendedGroup.find(
            {
                "source": source,
                "memberIds": user_oid,
                "idOnTheSource": {"$nin": group_ids},
            }
        ).update_many({"$pullAll": {"memberIds": [user_oid]}})

    async def ensure_group_principal_exists(self, group_id: str) -> None:
        """Snapshot all transitive group members into ExtendedGroup.memberIds before ACL grant.

        Dispatch is based on the group's own ``source`` field. LOCAL (or any source without a
        configured/enabled client) is a no-op. Port of PermissionService.ensureGroupPrincipalExists.
        Errors from the directory client are re-raised so the ACL grant route returns 500.
        """
        group = await ExtendedGroup.get(PydanticObjectId(group_id))
        if group is None:
            return
        client = self._clients.get(group.source)
        if client is None or not group.idOnTheSource:
            return

        try:
            member_oids = await client.get_group_members(group.idOnTheSource)
        except Exception:
            logger.error(
                "Failed to fetch group members for group %s (idOnTheSource=%s); ACL grant aborted.",
                group_id,
                group.idOnTheSource,
                exc_info=True,
            )
            raise
        await group.set({"memberIds": member_oids})
