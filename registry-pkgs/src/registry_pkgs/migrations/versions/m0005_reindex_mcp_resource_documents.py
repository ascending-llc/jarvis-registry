"""Strip per-user resources from OAuth MCP servers and queue a vector rebuild for servers with resources.

Step 1: an OAuth server's `config.resources` was listed with the token of whoever last refreshed it, so it is
that user's data (file names, document URIs) shown to every viewer through the detail API and discovery.
Clear it; the app no longer lists concrete resources for OAuth servers.

Step 2: resource documents changed (concrete URIs are labelled `URI:` instead of `URI template:`, and MCP
App `ui://` documents are no longer indexed), so stamp the vector-sync marker on every server that still has
resources. The reconciler picks markers older than its grace period and, with no plan, rebuilds the server's
documents in full.

Idempotent: step 1 no longer matches stripped documents; step 2 skips servers that are already pending. Step 1
stamps a fresh marker even on an OAuth server that was already pending, which resets that server's backoff and
costs at most one extra rebuild. A re-run after the reconciler cleared a marker stamps it again, which costs one
extra rebuild producing identical documents.

Rollout overlap: while old-image pods run, their reconciler can consume step 2's markers and rebuild with the old
documents (`URI template:` labels, `ui://` documents), and an old-image refresh can re-store an OAuth server's
resources. New code never indexes or returns an OAuth server's resources. To bring every index to the new
format, delete this migration's record once the rollout has finished so it runs again from the new image.
"""

import asyncio
import logging
from datetime import UTC, datetime

from pymongo import UpdateOne
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_HAS_RESOURCES = {"config.resources.0": {"$exists": True}}


def _is_oauth(config: dict) -> bool:
    """Copied, not imported, from ExtendedMCPServer.mcp_auth_mode as of this migration.

    AgentCore runtimes (truthy runtimeAccess) use shared credentials even when an `oauth` key is present.
    """
    if config.get("runtimeAccess"):
        return False
    return bool(config.get("requiresOAuth", False)) or "oauth" in config


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

    projection = {"config.requiresOAuth": 1, "config.oauth": 1, "config.runtimeAccess": 1}
    async for doc in servers.find(_HAS_RESOURCES, projection):
        if not _is_oauth(doc.get("config") or {}):
            continue
        matched += 1
        ops.append(
            UpdateOne(
                {"_id": doc["_id"], **_HAS_RESOURCES},
                {"$set": {"config.resources": [], "vectorSyncPendingAt": now, "vectorSyncAttempts": 0}},
            )
        )
        if len(ops) >= _BATCH_SIZE:
            modified += await flush()

    modified += await flush()
    logger.info("stripped OAuth mcpserver concrete resources: matched=%d modified=%d", matched, modified)

    result = await servers.update_many(
        {**_HAS_RESOURCES, "vectorSyncPendingAt": None},
        {"$set": {"vectorSyncPendingAt": now, "vectorSyncAttempts": 0}},
    )
    logger.info(
        "queued mcpserver resource-document rebuild: matched=%d modified=%d",
        result.matched_count,
        result.modified_count,
    )
