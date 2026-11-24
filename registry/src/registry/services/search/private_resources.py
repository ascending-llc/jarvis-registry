"""Read-time guard that keeps stale vectors from exposing OAuth servers' concrete resources.

A concrete resource listed with one user's OAuth token is that user's data. Capture no longer lists them and
indexing skips them, but vectors written before that (or by an old pod during a rollout) survive until the
server's next rebuild, and a rebuild can be delayed or fail. Every search path that returns MCP resource
documents, or builds results from them, filters them against the server's current authentication here.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from beanie import PydanticObjectId
from pymongo import ReadPreference

from registry_pkgs.models.enums import McpAuthMode, MCPEntityType
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer, mcp_auth_mode_from_config

_AUTH_PROJECTION = {"config.requiresOAuth": 1, "config.oauth": 1, "config.runtimeAccess": 1}
# Refill bounds: at most this many search rounds, and a window no larger than this (or the
# requested limit, if larger). Stays well under Weaviate's QUERY_MAXIMUM_RESULTS.
_MAX_REFILL_FETCHES = 4
_MAX_CANDIDATE_WINDOW = 200


def _is_concrete_resource(doc: dict[str, Any]) -> bool:
    # A resource without template metadata is treated as concrete, including legacy documents without a URI.
    return doc.get("entity_type") == MCPEntityType.RESOURCE and (
        bool(doc.get("resource_uri")) or not doc.get("resource_uri_template")
    )


async def drop_private_mcp_resources(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop concrete resource documents unless their server currently uses shared credentials.

    Whole documents are dropped, since their description also names the resource. Missing or deleted servers
    and invalid IDs fail closed; database errors propagate. Tools, prompts and resource templates are kept
    without a database lookup.
    """
    concrete = [doc for doc in docs if _is_concrete_resource(doc)]
    if not concrete:
        return docs

    server_ids = {
        PydanticObjectId(doc["server_id"]) for doc in concrete if PydanticObjectId.is_valid(doc.get("server_id"))
    }
    shared_server_ids: set[str] = set()
    if server_ids:
        # Primary reads: a just-committed switch to OAuth must apply at once.
        collection = ExtendedMCPServer.get_pymongo_collection().with_options(read_preference=ReadPreference.PRIMARY)
        async for server in collection.find({"_id": {"$in": list(server_ids)}}, _AUTH_PROJECTION):
            if mcp_auth_mode_from_config(server.get("config")) is not McpAuthMode.OAUTH:
                shared_server_ids.add(str(server["_id"]))

    return [doc for doc in docs if not _is_concrete_resource(doc) or doc.get("server_id") in shared_server_ids]


async def fetch_eligible_mcp_candidates(
    fetch: Callable[[int], Awaitable[list[dict[str, Any]]]],
    limit: int,
) -> list[dict[str, Any]]:
    """Refill a ranked candidate window when stale private resources consume its slots.

    Each enlarged window is checked against current primary Mongo auth state. Only return
    eligible documents, preserving their order; stop when the budget is filled, the source
    is exhausted, or the window / round cap is reached. Each round re-runs the whole search,
    so the caps bound vector, rerank and Mongo work: a server with thousands of stale resource
    vectors yields a short result instead of an ever-growing query. No extra vector request is
    needed when the first window is already eligible.
    """
    max_window = max(limit, _MAX_CANDIDATE_WINDOW)
    requested = limit
    for _ in range(_MAX_REFILL_FETCHES):
        docs = await fetch(requested)
        eligible = await drop_private_mcp_resources(docs)
        if len(eligible) >= limit or len(docs) < requested or requested >= max_window:
            break
        requested = min(requested * 2, max_window)
    return eligible[:limit]
