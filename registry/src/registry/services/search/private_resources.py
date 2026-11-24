"""Filter concrete resources by their current, shared-credential capture before ranking."""

from typing import Any

from beanie import PydanticObjectId
from pymongo import ReadPreference

from registry_pkgs.models.enums import MCPEntityType
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.mcp_resource_capture import (
    RESOURCE_CAPTURE_CONTEXT_FIELDS,
    RESOURCE_CAPTURE_KEY,
    RESOURCE_CAPTURE_REVISION,
    RESOURCE_KIND,
    resource_capture_revision,
)

_CAPTURE_PROJECTION = {
    **{f"config.{key}": 1 for key in RESOURCE_CAPTURE_CONTEXT_FIELDS},
    f"config.{RESOURCE_CAPTURE_KEY}": 1,
    "config.resources": 1,
}


def _is_concrete_resource(doc: dict[str, Any]) -> bool:
    return doc.get("entity_type") == MCPEntityType.RESOURCE and (
        bool(doc.get("resource_uri")) or not doc.get("resource_uri_template")
    )


async def _current_captures(server_ids: list[str] | None) -> dict[str, str]:
    match: dict[str, Any] = {}
    if server_ids is not None:
        ids = [PydanticObjectId(value) for value in server_ids if PydanticObjectId.is_valid(value)]
        if not ids:
            return {}
        match["_id"] = {"$in": ids}
    collection = ExtendedMCPServer.get_pymongo_collection().with_options(read_preference=ReadPreference.PRIMARY)
    captures = {}
    async for server in collection.find(match, _CAPTURE_PROJECTION):
        revision = resource_capture_revision(server.get("config") or {})
        if revision:
            captures[str(server["_id"])] = revision
    return captures


async def with_resource_visibility(filters: dict[str, Any], allowed_server_ids: list[str] | None) -> dict[str, Any]:
    """Exclude private/unsigned/stale concrete documents before vector top-k and reranking.

    Keep the caller's ACL and type filters in an explicit AND: the backend's boolean-filter parser
    treats $or as a complete expression rather than combining it with sibling fields.
    """
    captures = await _current_captures(allowed_server_ids)
    visible: list[dict[str, Any]] = [
        {"entity_type": {"$ne": MCPEntityType.RESOURCE.value}},
        {"entity_type": MCPEntityType.RESOURCE.value, RESOURCE_KIND: "template"},
    ]
    visible.extend(
        {
            "entity_type": MCPEntityType.RESOURCE.value,
            "server_id": server_id,
            RESOURCE_KIND: "concrete",
            RESOURCE_CAPTURE_REVISION: revision,
        }
        for server_id, revision in captures.items()
    )
    return {"$and": [filters, {"$or": visible}]}


async def drop_private_mcp_resources(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recheck current proofs after search to catch configuration changes during retrieval."""
    concrete = [doc for doc in docs if _is_concrete_resource(doc)]
    captures = await _current_captures([doc.get("server_id") for doc in concrete]) if concrete else {}
    return [
        {key: value for key, value in doc.items() if key not in (RESOURCE_CAPTURE_REVISION, RESOURCE_KIND)}
        for doc in docs
        if not _is_concrete_resource(doc)
        or (
            doc.get(RESOURCE_CAPTURE_REVISION) is not None
            and doc.get(RESOURCE_CAPTURE_REVISION) == captures.get(doc.get("server_id"))
        )
    ]
