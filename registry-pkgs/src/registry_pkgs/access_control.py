"""Shared ACL principal resolution used by registry services and workflows."""

from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.models import Group, PrincipalType, User


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
    if user is None or not user.idOnTheSource:
        return []
    groups = await Group.find({"memberIds": user.idOnTheSource}, session=session).to_list()
    return [group.id for group in groups]
