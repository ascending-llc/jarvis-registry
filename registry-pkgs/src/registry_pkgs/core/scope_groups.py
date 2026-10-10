"""Resolve trusted IdP group identities to the abstract groups in scopes.yml."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from .config import JarvisBaseSettings

SCOPE_GROUP_OBJECT_ID_FIELDS: dict[str, str] = {
    "jarvis-registry-admin": "jarvis_registry_admin_group_object_id",
    "jarvis-registry-power-user": "jarvis_registry_power_user_group_object_id",
    "jarvis-registry-user": "jarvis_registry_user_group_object_id",
    "jarvis-registry-read-only": "jarvis_registry_read_only_group_object_id",
}


def validate_scope_group_object_ids(values: Mapping[str, str | None]) -> dict[str, str]:
    """Return canonical UUIDs by field name, reporting all missing, invalid or duplicate bindings."""
    normalized: dict[str, str] = {}
    fields_by_id: dict[str, list[str]] = {}
    errors: list[str] = []
    for field in SCOPE_GROUP_OBJECT_ID_FIELDS.values():
        value = values.get(field)
        if not value or not value.strip():
            errors.append(f"{field.upper()} is not set")
            continue
        try:
            object_id = str(UUID(value.strip()))
        except ValueError:
            errors.append(f"{field.upper()} must be a UUID")
            continue
        normalized[field] = object_id
        fields_by_id.setdefault(object_id, []).append(field.upper())

    for fields in fields_by_id.values():
        if len(fields) > 1:
            errors.append(f"{', '.join(fields)} must have distinct group object IDs")
    if errors:
        raise ValueError("; ".join(errors))
    return normalized


def entra_scope_group_ids(settings: JarvisBaseSettings) -> dict[str, str]:
    """Read configured bindings; Google-only deployments may have none."""
    return {
        str(UUID(value.strip())): group
        for group, field in SCOPE_GROUP_OBJECT_ID_FIELDS.items()
        if (value := getattr(settings, field))
    }


def scope_groups_for_entra_ids(
    object_ids: Iterable[str],
    configured_groups: Mapping[str, str],
) -> list[str]:
    """Ignore unknown/malformed IDs and return matches once, in configured order."""
    member_ids: set[str] = set()
    for object_id in object_ids:
        try:
            member_ids.add(str(UUID(object_id.strip())))
        except ValueError:
            continue
    return [group for object_id, group in configured_groups.items() if object_id in member_ids]


def scope_group_for_google_email(group_email: str | None, allowed_hd: str) -> str | None:
    """Accept a group's local part only when its complete domain is explicitly allowed."""
    if not group_email or not allowed_hd:
        return None
    email = group_email.strip()
    local_part, separator, domain = email.partition("@")
    if not separator or not local_part or not domain or "@" in domain or any(char.isspace() for char in email):
        return None
    if domain.casefold() != allowed_hd.strip().casefold():
        return None
    return local_part
