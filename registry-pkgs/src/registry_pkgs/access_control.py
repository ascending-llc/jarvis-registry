"""Shared ACL principal resolution used by registry services and workflows."""

from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.models import ExtendedGroup, PrincipalType, User


def group_member_id_for_user(user: User) -> str | None:
    """Return the value that identifies this user in ``groups.memberIds``, or None.

    Google users are matched on their current primary ``email`` (Cloud Identity group
    ``memberIds`` are emails, and ``email`` stays current on every login); ``idOnTheSource``
    is not consulted for them. Every other provider keeps ``idOnTheSource`` as the key.
    """
    if user.provider == "google":
        return user.email or None
    return user.idOnTheSource or None


def build_acl_principal_or_clause(
    user_id: PydanticObjectId,
    group_ids: list[PydanticObjectId],
) -> list[dict[str, Any]]:
    """Build a USER/GROUP/PUBLIC principal clause with stable semantics."""
    clauses: list[dict[str, Any]] = [
        {"principalType": PrincipalType.USER.value, "principalId": user_id},
        {"principalType": PrincipalType.PUBLIC.value, "principalId": None},
    ]
    if group_ids:
        clauses.append({"principalType": PrincipalType.GROUP.value, "principalId": {"$in": group_ids}})
    return clauses


async def resolve_group_ids_for_user(
    user_id: PydanticObjectId,
    *,
    session: Any | None = None,
) -> list[PydanticObjectId]:
    """Return current MongoDB group IDs for a user."""
    user = await User.get(user_id, session=session)
    if user is None:
        return []
    member_id = group_member_id_for_user(user)
    if member_id is None:
        return []
    groups = await ExtendedGroup.find({"memberIds": member_id}, session=session).to_list()
    return [group.id for group in groups]
