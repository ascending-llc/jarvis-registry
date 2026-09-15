#!/usr/bin/env python3
"""
Backfill `normalizedServerName` on the shared `mcpservers` collection (AS-1855) and ensure its
unique partial index exists.

Usage:
    uv run python scripts/backfill_mcp_server_normalized_names.py [--dry-run] [--force]

Arguments:
    --dry-run: Report conflicts and pending updates without writing anything, and without
               creating the index.
    --force: Skip interactive confirmation (required for non-interactive CI use).
"""

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer, normalize_server_name

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")
MONGODB_USERNAME = os.environ.get("MONGODB_USERNAME", "")
MONGODB_PASSWORD = os.environ.get("MONGODB_PASSWORD", "")

NORMALIZED_INDEX_NAME = (
    "normalizedServerName_1_tenantId_1"  # must match packages/data-schemas/src/migrations/mcpServerNames.ts:102 exactly
)


def _get_server_label(doc: dict[str, Any]) -> str:
    return str(doc.get("serverName") or doc.get("_id") or "unknown")


def find_normalized_name_conflicts(docs: list[dict]) -> dict[tuple[str | None, str], list[dict]]:
    """Group by (tenantId, normalize_server_name(serverName)); return only groups with >1 doc.

    Mirrors mcpServerNames.ts's `identities` map (packages/data-schemas/src/migrations/mcpServerNames.ts:52-66) —
    same (tenantId, normalizedName) scoping, so Registry's conflict detection matches Chat's
    actual unique index boundary. Missing tenantId groups together as one bucket (matches how a
    compound unique index treats a missing field as null) — this is the common case for every
    Registry-created server, none of which set tenantId. Note this mirrors only the grouping
    key, not the control flow: mcpServerNames.ts is fail-fast (throws on the first duplicate it
    finds during a single scan), while this function collects every conflicting group up front
    so --dry-run can report all of them at once.
    """
    groups: defaultdict[tuple[str | None, str], list[dict]] = defaultdict(list)
    for doc in docs:
        server_name = doc.get("serverName")
        if not server_name:
            continue
        key = (doc.get("tenantId"), normalize_server_name(server_name))
        groups[key].append(doc)

    return {key: group for key, group in groups.items() if len(group) > 1}


async def ensure_normalized_name_index(collection) -> bool:
    """Create the unique partial index only if absent; no-op (not an error) if already present.

    DEMO and PROD already have this index — this function must return without attempting
    creation there. Passing the identical index name Chat's own migration uses
    (NORMALIZED_INDEX_NAME) guarantees no name/spec mismatch regardless of which side created it
    first — see the 'index already exists' incident referenced in Change 2.
    """
    existing = await collection.index_information()
    if NORMALIZED_INDEX_NAME in existing:
        return False

    await collection.create_index(
        [("normalizedServerName", 1), ("tenantId", 1)],
        name=NORMALIZED_INDEX_NAME,
        unique=True,
        partialFilterExpression={"normalizedServerName": {"$exists": True}},
    )
    return True


async def backfill_normalized_names(dry_run: bool) -> dict:
    """Fetch all docs, report conflicts (see find_normalized_name_conflicts), abort without
    writing if any exist. Otherwise, for each doc whose normalizedServerName is missing or
    doesn't match normalize_server_name(serverName), `update_one` with $set (batched, not a
    single transaction — matches migrate_a2a_agent_path_slug.py and mcpServerNames.ts's own
    bulkWrite-in-batches approach). Then call ensure_normalized_name_index unless dry_run.
    Returns stats: total, conflicts_found, updated, index_created.
    """
    stats = {
        "total": 0,
        "conflicts_found": 0,
        "updated": 0,
        "index_created": False,
    }

    collection = ExtendedMCPServer.get_pymongo_collection()

    logger.info("Fetching all documents from mcpservers...")
    docs = await collection.find({}).to_list(length=None)
    stats["total"] = len(docs)
    logger.info(f"Found {stats['total']} documents")

    if stats["total"] == 0:
        logger.info("No documents found. Skipping backfill and continuing to index creation.")
    else:
        logger.info("Checking for normalizedServerName conflicts...")
        conflicts = find_normalized_name_conflicts(docs)
        stats["conflicts_found"] = len(conflicts)

        if conflicts:
            logger.error(f"Found {stats['conflicts_found']} normalizedServerName conflicts!")
            logger.error("The following (tenantId, normalizedServerName) groups have more than one document:")
            for (tenant_id, normalized_name), conflict_docs in conflicts.items():
                logger.error(f"\n  tenantId={tenant_id!r} normalizedServerName='{normalized_name}'")
                for doc in conflict_docs:
                    logger.error(
                        "    - ID: %s, serverName: '%s'",
                        doc.get("_id"),
                        doc.get("serverName"),
                    )

            logger.error("\nYou must manually resolve these conflicts before the backfill can proceed.")
            logger.error("Suggested actions:")
            logger.error("  1. Manually rename one of the conflicting documents' serverName")
            logger.error("  2. Re-run this script")
            return stats

        logger.info("No conflicts detected. Proceeding with backfill...")

        if dry_run:
            logger.info("DRY RUN MODE: Simulating changes...")

        for doc in docs:
            server_name = doc.get("serverName")
            if not server_name:
                logger.warning("Document %s has no serverName, skipping", doc.get("_id"))
                continue

            expected_normalized_name = normalize_server_name(server_name)
            if doc.get("normalizedServerName") == expected_normalized_name:
                logger.debug("Already up to date: '%s' (%s)", server_name, doc.get("_id"))
                continue

            logger.info(
                "Backfilling normalizedServerName: '%s' -> '%s' (%s)",
                doc.get("normalizedServerName"),
                expected_normalized_name,
                _get_server_label(doc),
            )

            if not dry_run:
                await collection.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"normalizedServerName": expected_normalized_name}},
                )
                stats["updated"] += 1

    if not dry_run:
        logger.info("Ensuring 'normalizedServerName_1_tenantId_1' index exists...")
        stats["index_created"] = await ensure_normalized_name_index(collection)
        if stats["index_created"]:
            logger.info("Created index '%s'", NORMALIZED_INDEX_NAME)
        else:
            logger.info("Index '%s' already exists, skipping creation", NORMALIZED_INDEX_NAME)

    return stats


async def main(dry_run: bool = False) -> None:
    """Main entrypoint"""
    logger.info("=" * 80)
    logger.info("mcpservers normalizedServerName Backfill Script")
    logger.info("=" * 80)

    if dry_run:
        logger.info("Running in DRY RUN mode - no changes will be made")

    logger.info("Connecting to MongoDB using shared connection configuration")
    try:
        await MongoDB.connect_db(
            config=MongoConfig(
                mongo_uri=MONGO_URI,
                mongodb_username=MONGODB_USERNAME,
                mongodb_password=MONGODB_PASSWORD,
            )
        )
        logger.info("Connected to MongoDB successfully")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

    try:
        stats = await backfill_normalized_names(dry_run=dry_run)

        logger.info("\n" + "=" * 80)
        logger.info("Backfill Summary")
        logger.info("=" * 80)
        logger.info(f"Total documents found:    {stats['total']}")
        logger.info(f"Conflicts found:          {stats['conflicts_found']}")
        logger.info(f"Documents updated:        {stats['updated']}")
        logger.info(f"Index created:            {stats['index_created']}")

        if stats["conflicts_found"] > 0:
            logger.error("\nBackfill FAILED due to conflicts. Please resolve conflicts and try again.")
            sys.exit(1)

        if dry_run:
            logger.info("\nDRY RUN completed. Run without --dry-run to apply changes.")
        else:
            logger.info("\nBackfill completed successfully!")

    except Exception as e:
        logger.error(f"Backfill failed with error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill normalizedServerName on the mcpservers collection")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report conflicts and pending updates without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive confirmation",
    )

    args = parser.parse_args()

    if not args.dry_run and not args.force:
        print("\n" + "=" * 80)
        print("WARNING: This migration will modify your database!")
        print("=" * 80)
        print("This script will:")
        print("  1. Backfill normalizedServerName on every mcpservers document where it's missing or stale")
        print("  2. Create the normalizedServerName_1_tenantId_1 unique index if it doesn't already exist")
        print("\nRecommendation: Run with --dry-run first to check for conflicts")
        print("=" * 80)

        response = input("\nDo you want to continue? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("Migration cancelled.")
            sys.exit(0)

    asyncio.run(main(dry_run=args.dry_run))
