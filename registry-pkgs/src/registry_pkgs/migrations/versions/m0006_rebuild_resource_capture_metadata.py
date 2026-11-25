"""Queue a resource recapture for legacy shared lists and rebuild resource vectors with capture metadata.

Step 1: concrete resource lists stored before capture proofs existed are hidden until a capture with the
current shared credentials signs them. Mark every shared-credential server whose list has no proof with
`resourceRecapturePendingAt`; the Registry's resource-recapture reconciler refreshes each one through the
normal capture path, so no list stays hidden waiting for a manual refresh. This migration never calls a
downstream server itself.

Step 2: resource documents gained kind and capture-revision metadata, so queue a vector rebuild for every
server with resources or resource templates. The policy marker makes reruns no-ops even after the reconciler
clears vectorSyncPendingAt.

Idempotent: step 1 skips servers that already have a proof or a pending recapture; step 2 skips marked servers.
A re-run after the reconciler gave up on an unreachable server queues it again, which costs one more attempt
cycle. Rollout overlap: old pods ignore the additive fields, and an old-pod capture writes no proof, so that
server simply stays queued until a new pod recaptures it.
"""

import asyncio
import logging
from datetime import UTC, datetime

from pymongo import UpdateOne
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_AUTH_FIELDS = ("requiresOAuth", "oauth", "runtimeAccess")


def _uses_shared_credentials(config: dict) -> bool:
    """Copied, not imported, from mcp_resource_capture.uses_shared_credentials as of this migration."""
    return bool(config.get("runtimeAccess")) or not (config.get("requiresOAuth") or "oauth" in config)


async def up(db: AsyncDatabase) -> None:
    servers = db["mcpservers"]
    now = datetime.now(UTC)

    ops: list[UpdateOne] = []
    matched = 0
    modified = 0

    async def flush() -> int:
        nonlocal ops
        if not ops:
            return 0
        result = await servers.bulk_write(ops, ordered=False)
        ops = []
        await asyncio.sleep(0)
        return result.modified_count

    unsigned = {
        "config.resources.0": {"$exists": True},
        "config.resourceCapture": {"$exists": False},
        "resourceRecapturePendingAt": {"$exists": False},
    }
    projection = {f"config.{key}": 1 for key in _AUTH_FIELDS}
    async for doc in servers.find(unsigned, projection):
        config = doc.get("config") or {}
        if not _uses_shared_credentials(config):
            continue
        matched += 1
        # Skip a server whose authentication changed concurrently; the app re-evaluates it on its next capture.
        auth_snapshot = {
            f"config.{key}": ({"$exists": True, "$eq": config[key]} if key in config else {"$exists": False})
            for key in _AUTH_FIELDS
        }
        ops.append(
            UpdateOne(
                {"_id": doc["_id"], **unsigned, **auth_snapshot},
                {"$set": {"resourceRecapturePendingAt": now, "resourceRecaptureAttempts": 0}},
            )
        )
        if len(ops) >= _BATCH_SIZE:
            modified += await flush()

    modified += await flush()
    logger.info("queued shared resource recapture: matched=%d modified=%d", matched, modified)

    result = await servers.update_many(
        {
            "config.resourceVisibilityVersion": {"$ne": 1},
            "$or": [
                {"config.resources.0": {"$exists": True}},
                {"config.resourceTemplates.0": {"$exists": True}},
            ],
        },
        {
            "$set": {
                "config.resourceVisibilityVersion": 1,
                "vectorSyncPendingAt": now,
                "vectorSyncAttempts": 0,
            }
        },
    )
    logger.info(
        "queued resource visibility rebuild: matched=%d modified=%d", result.matched_count, result.modified_count
    )
