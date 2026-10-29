"""Rename camelCase `apiKey` sub-fields of MCP servers to the shared snake_case contract.

Jarvis Chat's `mcpservers` schema and Registry's invocation path read `config.apiKey.authorization_type`
and `config.apiKey.custom_header`. Older Registry code stored the REST payload's `authorizationType` /
`customHeader` as received, so the invocation path fell back to Bearer and sent the wrong header.

Scope: `mcpservers` documents that have `config.apiKey.authorizationType` or `config.apiKey.customHeader`.
`source` and `key` are spelled the same either way and are never read or written; `key` stays encrypted.

Merge rule: camelCase wins. The UI only ever writes camelCase, so in a document holding both spellings
the camelCase value is from the most recent save.

Idempotent: each write `$unset`s the camelCase fields it `$set`s, so a re-run matches nothing and a
partial run resumes on the documents still matched. Each write is a compare-and-swap on the camelCase
values read, so a concurrent app save turns it into a no-op instead of being clobbered. An old-code pod
can still write camelCase after this runs; delete this migration's record to re-run it.
"""

import asyncio
import logging

from pymongo import UpdateOne
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

# Copied, not imported: this migration must keep matching the records it was written for.
# (camelCase field, snake_case field) pairs under config.apiKey.
_FIELD_PAIRS = [
    ("authorizationType", "authorization_type"),
    ("customHeader", "custom_header"),
]
_BATCH_SIZE = 500


async def up(db: AsyncDatabase) -> None:
    servers = db["mcpservers"]
    match = {"$or": [{f"config.apiKey.{camel}": {"$exists": True}} for camel, _ in _FIELD_PAIRS]}
    projection = {f"config.apiKey.{camel}": 1 for camel, _ in _FIELD_PAIRS}

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

    async for doc in servers.find(match, projection):
        apikey = (doc.get("config") or {}).get("apiKey") or {}
        set_fields = {}
        unset_fields = {}
        cas_filter = {"_id": doc["_id"]}
        for camel, snake in _FIELD_PAIRS:
            if camel in apikey:
                set_fields[f"config.apiKey.{snake}"] = apikey[camel]
                unset_fields[f"config.apiKey.{camel}"] = ""
                # Scope the write to the value we read, so a concurrent edit no-ops this doc.
                cas_filter[f"config.apiKey.{camel}"] = apikey[camel]
        if not set_fields:
            continue
        matched += 1
        ops.append(UpdateOne(cas_filter, {"$set": set_fields, "$unset": unset_fields}))
        if len(ops) >= _BATCH_SIZE:
            modified += await flush()

    modified += await flush()
    logger.info("snake_cased mcpserver apiKey fields: matched=%d modified=%d", matched, modified)
