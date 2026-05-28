#!/usr/bin/env python3
"""
Data Migration Script: Merge A2AAgent.slug into A2AAgent.path

This script migrates the A2AAgent collection by:
1. Detecting and reporting any path conflicts before migration
2. Converting all path values to slug format (no slashes)
3. Removing the slug field from all documents
4. Dropping the slug unique index

Usage:
    cd /path/to/mcp-gateway-registry
    uv run python scripts/migrate_a2a_agent_path_slug.py [--dry-run] [--force]

Arguments:
    --dry-run: Check for conflicts without making changes
    --force: Skip interactive confirmation
"""

import argparse
import asyncio
import logging
import sys
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent, normalize_a2a_agent_path

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _get_agent_name(agent: dict[str, Any]) -> str:
    card = agent.get("card")
    if isinstance(card, dict):
        return str(card.get("name") or "unknown")
    return "unknown"


def check_path_conflicts(agents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Check for path conflicts after normalization.

    Returns:
        Dictionary mapping normalized paths to list of agents that would conflict
    """
    path_map: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for agent in agents:
        normalized_path = normalize_a2a_agent_path(agent.get("path"))
        path_map[normalized_path].append(agent)

    # Return only conflicting paths
    conflicts = {path: agents for path, agents in path_map.items() if len(agents) > 1}
    return conflicts


async def migrate_agents(dry_run: bool = False) -> dict:
    """
    Perform the migration.

    Returns:
        Migration statistics dictionary
    """
    stats = {
        "total_agents": 0,
        "agents_updated": 0,
        "paths_normalized": 0,
        "slug_fields_removed": 0,
        "conflicts_found": 0,
    }

    collection = A2AAgent.get_pymongo_collection()

    # Step 1: Fetch all agents
    logger.info("Fetching all A2A agents from database...")
    agents = await collection.find({}).to_list(length=None)
    stats["total_agents"] = len(agents)
    logger.info(f"Found {stats['total_agents']} agents")

    if stats["total_agents"] == 0:
        logger.info("No agents found. Skipping path normalization and continuing index/field cleanup.")
    else:
        # Step 2: Check for conflicts
        logger.info("Checking for path conflicts after normalization...")
        conflicts = check_path_conflicts(agents)
        stats["conflicts_found"] = len(conflicts)

        if conflicts:
            logger.error(f"Found {stats['conflicts_found']} path conflicts!")
            logger.error("The following paths would conflict after normalization:")
            for normalized_path, conflict_agents in conflicts.items():
                logger.error(f"\n  Normalized path: '{normalized_path}'")
                for agent in conflict_agents:
                    logger.error(
                        "    - ID: %s, Original path: '%s', Name: %s",
                        agent.get("_id"),
                        agent.get("path"),
                        _get_agent_name(agent),
                    )

            logger.error("\nYou must manually resolve these conflicts before migration can proceed.")
            logger.error("Suggested actions:")
            logger.error("  1. Manually rename one of the conflicting agents to a different path")
            logger.error("  2. Re-run this migration script")
            return stats

        logger.info("No conflicts detected. Proceeding with migration...")

    if dry_run:
        logger.info("DRY RUN MODE: Simulating changes...")

    # Step 3: Update all agent documents
    for agent in agents:
        original_path = agent.get("path")
        normalized_path = normalize_a2a_agent_path(original_path)

        if original_path != normalized_path:
            stats["paths_normalized"] += 1
            logger.info(
                "Normalizing path: '%s' -> '%s' (Agent: %s)",
                original_path,
                normalized_path,
                _get_agent_name(agent),
            )

            if not dry_run:
                await collection.update_one(
                    {"_id": agent["_id"]},
                    {"$set": {"path": normalized_path}},
                )
                stats["agents_updated"] += 1
        else:
            logger.debug("Path already normalized: '%s' (Agent: %s)", original_path, _get_agent_name(agent))

    # Step 4: Drop slug index
    if not dry_run:
        logger.info("Dropping 'slug' unique index...")
        try:
            await collection.drop_index("slug_1")
            logger.info("Successfully dropped 'slug_1' index")
        except Exception as e:
            logger.warning(f"Could not drop 'slug_1' index (may not exist): {e}")

    # Step 5: Remove slug field from all documents
    if not dry_run:
        logger.info("Removing 'slug' field from all documents...")
        result = await collection.update_many({"slug": {"$exists": True}}, {"$unset": {"slug": ""}})
        stats["slug_fields_removed"] = result.modified_count
        logger.info(f"Removed 'slug' field from {stats['slug_fields_removed']} documents")

    return stats


async def main(dry_run: bool = False, force: bool = False) -> None:
    """Main migration function"""
    logger.info("=" * 80)
    logger.info("A2AAgent Path/Slug Migration Script")
    logger.info("=" * 80)

    if dry_run:
        logger.info("Running in DRY RUN mode - no changes will be made")

    # Connect to MongoDB using the shared project connection manager.
    logger.info("Connecting to MongoDB using shared connection configuration")
    try:
        await MongoDB.connect_db(config=MongoConfig())
        logger.info("Connected to MongoDB successfully")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

    # Run migration
    try:
        stats = await migrate_agents(dry_run=dry_run)

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("Migration Summary")
        logger.info("=" * 80)
        logger.info(f"Total agents found:       {stats['total_agents']}")
        logger.info(f"Paths normalized:         {stats['paths_normalized']}")
        logger.info(f"Agents updated:           {stats['agents_updated']}")
        logger.info(f"Slug fields removed:      {stats['slug_fields_removed']}")
        logger.info(f"Conflicts found:          {stats['conflicts_found']}")

        if stats["conflicts_found"] > 0:
            logger.error("\nMigration FAILED due to conflicts. Please resolve conflicts and try again.")
            sys.exit(1)

        if dry_run:
            logger.info("\nDRY RUN completed. Run without --dry-run to apply changes.")
        else:
            logger.info("\nMigration completed successfully!")
            logger.info("All A2A agents have been migrated to use the 'path' field without slashes.")
            logger.info("The 'slug' field and index have been removed.")

    except Exception as e:
        logger.error(f"Migration failed with error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await MongoDB.close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate A2AAgent path and slug fields")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check for conflicts without making changes",
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
        print("  1. Normalize all agent paths to slug format (no slashes)")
        print("  2. Remove the 'slug' field from all documents")
        print("  3. Drop the 'slug' unique index")
        print("\nRecommendation: Run with --dry-run first to check for conflicts")
        print("=" * 80)

        response = input("\nDo you want to continue? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("Migration cancelled.")
            sys.exit(0)

    asyncio.run(main(dry_run=args.dry_run, force=args.force))
