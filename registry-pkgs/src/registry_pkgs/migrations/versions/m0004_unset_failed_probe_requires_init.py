"""Unset MCP server initialization flags likely overwritten by failed capability probes.

Scope: `mcpservers` with `config.requiresInit: false` and an error at or after their
last successful connection (or no successful connection). Removing the flag restores
the gateway's default of initializing, including for old-code pods.

This filter is a heuristic: failed toggle-enable probes do not set `lastError`, so
those records may be missed. A successful refresh restores the detected value.
Old-code pods can still overwrite the flag during a rolling deployment.

Idempotent: unset records no longer match `config.requiresInit: false`.
"""

import logging

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)


async def up(db: AsyncDatabase) -> None:
    result = await db["mcpservers"].update_many(
        {
            "config.requiresInit": False,
            "lastError": {"$ne": None},
            "$or": [
                {"lastConnected": None},
                {"$expr": {"$gte": ["$lastError", "$lastConnected"]}},
            ],
        },
        {"$unset": {"config.requiresInit": ""}},
    )
    logger.info(
        "Unset failed-probe requiresInit flags: matched=%d modified=%d",
        result.matched_count,
        result.modified_count,
    )
