"""Remove duplicate Registry MCP OAuth token records.

Registry's token commit refuses to read or write a (userId, type, identifier) that has more than
one record, so a user with legacy duplicates can neither log in nor refresh until they are removed.
Older code checked for a record and then inserted, so concurrent logins could create duplicates.

Scope: only Registry's own `registry:mcp:*` identifiers with the MCP OAuth access/refresh types.
Jarvis Chat's `mcp:*` records in the shared `tokens` collection are never matched.

Survivor: the minimum `_id` per key. Older code reads and updates the first record `find_one`
returns, which in practice is the earliest inserted, so that record holds the live credential.

Idempotent: once a key has one record it no longer forms a group, so a re-run deletes nothing.
Only the `_id`s seen in this run are deleted, never "everything but the survivor", so a record
written concurrently is left for a later run instead of being removed blindly.
"""

import asyncio
import logging

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

# Copied, not imported: this migration must keep matching the records it was written for.
_REGISTRY_IDENTIFIER_PATTERN = "^registry:mcp:"
_TOKEN_TYPES = ["mcp_oauth", "mcp_oauth_refresh"]


async def up(db: AsyncDatabase) -> None:
    tokens = db["tokens"]
    pipeline = [
        {"$match": {"identifier": {"$regex": _REGISTRY_IDENTIFIER_PATTERN}, "type": {"$in": _TOKEN_TYPES}}},
        {
            "$group": {
                "_id": {"userId": "$userId", "type": "$type", "identifier": "$identifier"},
                "ids": {"$push": "$_id"},
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
    ]
    groups = 0
    deleted = 0
    async for group in await tokens.aggregate(pipeline):
        survivor = min(group["ids"])
        losers = [doc_id for doc_id in group["ids"] if doc_id != survivor]
        result = await tokens.delete_many({"_id": {"$in": losers}, **group["_id"]})
        groups += 1
        deleted += result.deleted_count
        await asyncio.sleep(0)
    logger.info("deduplicated registry OAuth tokens: groups=%d deleted=%d", groups, deleted)
