#!/usr/bin/env python3
"""Repair historical Entra identity fields without initializing models or changing indexes.

Run from the repository root with MongoDB settings supplied by the environment or .env:
    uv run python scripts/backfill_user_openid_issuer.py --issuer <Entra-v2-URL> --dry-run
Remove --dry-run to apply the previewed changes after confirmation; --force skips confirmation.
"""

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from registry_pkgs.core.config import MongoSettings
from registry_pkgs.database.mongodb import create_mongo_client

logger = logging.getLogger(__name__)

ENTRA_ISSUER_PATTERN = re.compile(r"https://login\.microsoftonline\.com/([0-9a-f-]{36})/v2\.0")
MISSING_ISSUER_FILTER: dict[str, Any] = {
    "provider": "openid",
    "openidIssuer": {"$exists": False},
    "$or": [{"openidId": {"$nin": ["", None]}}, {"idOnTheSource": {"$exists": True}}],
}
EMPTY_ID_FILTER = {"openidId": ""}


class BackfillError(ValueError):
    """A precondition failed; its message contains no database values or credentials."""


@dataclass(frozen=True)
class BackfillChanges:
    issuer_ids: tuple[ObjectId, ...]
    empty_id_ids: tuple[ObjectId, ...]


def normalize_issuer(value: str) -> str:
    """Match the Entra v2 issuer contract, including a canonical tenant UUID."""
    issuer = value.rstrip("/")
    match = ENTRA_ISSUER_PATTERN.fullmatch(issuer)
    if match is not None:
        try:
            if str(UUID(match[1])) == match[1]:
                return issuer
        except ValueError:
            pass
    raise argparse.ArgumentTypeError("--issuer must be https://login.microsoftonline.com/<tenant-UUID>/v2.0")


async def collect_changes(db: AsyncDatabase, issuer: str) -> BackfillChanges:
    """Validate the single-issuer assumption and read only the IDs that would change."""
    users = db["users"]
    existing = await users.distinct("openidIssuer", {"provider": "openid"})
    if existing and existing != [issuer]:
        raise BackfillError("OpenID users contain a different or null issuer; no changes were made.")
    issuer_ids = [doc["_id"] async for doc in users.find(MISSING_ISSUER_FILTER, {"_id": 1})]
    empty_id_ids = [doc["_id"] async for doc in users.find(EMPTY_ID_FILTER, {"_id": 1})]
    return BackfillChanges(tuple(sorted(issuer_ids, key=str)), tuple(sorted(empty_id_ids, key=str)))


async def apply_changes(db: AsyncDatabase, issuer: str, changes: BackfillChanges) -> tuple[int, int]:
    """Recheck the preview and use conditional, idempotent updates restricted to its IDs."""
    if await collect_changes(db, issuer) != changes:
        raise BackfillError("Pending changes changed after the preview; rerun the script to review them.")
    users = db["users"]
    issuer_count = empty_id_count = 0
    if changes.issuer_ids:
        result = await users.update_many(
            {**MISSING_ISSUER_FILTER, "_id": {"$in": list(changes.issuer_ids)}},
            {"$set": {"openidIssuer": issuer}},
        )
        issuer_count = result.modified_count
    logger.info("openidIssuer: planned=%d modified=%d", len(changes.issuer_ids), issuer_count)
    if changes.empty_id_ids:
        result = await users.update_many(
            {**EMPTY_ID_FILTER, "_id": {"$in": list(changes.empty_id_ids)}},
            {"$unset": {"openidId": ""}},
        )
        empty_id_count = result.modified_count
    logger.info("Empty openidId: planned=%d modified=%d", len(changes.empty_id_ids), empty_id_count)
    return issuer_count, empty_id_count


async def run_backfill(db: AsyncDatabase, issuer: str, *, dry_run: bool, force: bool) -> tuple[int, int]:
    """Preview before prompting, including for forced runs; never write during dry-run."""
    changes = await collect_changes(db, issuer)
    for label, ids in (("openidIssuer", changes.issuer_ids), ("Empty openidId", changes.empty_id_ids)):
        logger.info("%s: planned=%d", label, len(ids))
        for user_id in ids:
            logger.info("  user_id=%s", user_id)
    if dry_run:
        logger.info("Dry-run complete; no changes were made.")
        return 0, 0
    if not changes.issuer_ids and not changes.empty_id_ids:
        logger.info("No changes needed.")
        return 0, 0
    if not force:
        try:
            answer = await asyncio.to_thread(input, "Apply these user identity changes? Type yes to continue: ")
        except EOFError as exc:
            raise BackfillError("Confirmation unavailable; use --force only after reviewing a dry-run.") from exc
        if answer.strip().lower() != "yes":
            logger.info("Cancelled; no changes were made.")
            return 0, 0
    return await apply_changes(db, issuer, changes)


async def async_main(args: argparse.Namespace) -> int:
    client, db_name = create_mongo_client(MongoSettings().mongo_config)
    try:
        await client.admin.command("ping")
        logger.info("Connected to MongoDB database %s", db_name)
        await run_backfill(client[db_name], args.issuer, dry_run=args.dry_run, force=args.force)
        return 0
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuer", required=True, type=normalize_issuer, help="Single Entra tenant v2 issuer URL")
    parser.add_argument("--dry-run", action="store_true", help="Preview IDs and counts without any writes")
    parser.add_argument("--force", action="store_true", help="Skip confirmation; all safety checks still apply")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except BackfillError as exc:
        logger.error("%s", exc)
    except (PyMongoError, ValueError) as exc:
        # Driver and settings exceptions may contain credentials or full documents.
        logger.error("Backfill failed (%s); verify MongoDB configuration and availability.", type(exc).__name__)
    except KeyboardInterrupt:
        logger.error("Interrupted; review the data and rerun to complete any pending changes.")
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
