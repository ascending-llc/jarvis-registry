"""Rebuild resource vectors with kind and capture-revision metadata.

Legacy concrete lists remain untrusted until successfully refreshed with shared credentials.
The policy marker makes reruns no-ops even after the reconciler clears vectorSyncPendingAt.
Never replace a concurrently captured list or proof; old pods tolerate the additive config key.
"""

import logging
from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)


async def up(db: AsyncDatabase) -> None:
    result = await db["mcpservers"].update_many(
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
                "vectorSyncPendingAt": datetime.now(UTC),
                "vectorSyncAttempts": 0,
            }
        },
    )
    logger.info(
        "queued resource visibility rebuild: matched=%d modified=%d", result.matched_count, result.modified_count
    )
