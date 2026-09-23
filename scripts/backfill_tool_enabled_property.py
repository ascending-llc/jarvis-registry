#!/usr/bin/env python3
"""
Backfill the weaviate `tool_enabled` property on every pre-existing MCP_Servers doc (AS-1861).

Usage:
    uv run python scripts/backfill_tool_enabled_property.py [--dry-run] [--force]

Arguments:
    --dry-run: Report how many servers would be patched without writing anything.
    --force: Skip interactive confirmation (required for non-interactive CI use).
"""

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

from registry.core.config import Settings
from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.vector.client import create_database_client
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository


async def backfill_tool_enabled(dry_run: bool) -> dict:
    stats = {"total": 0, "patched": 0, "failed": 0}

    settings = Settings()
    await init_mongodb(settings.mongo_config)
    db_client = create_database_client(settings.vector_backend_config)
    try:
        mcp_repo = MCPServerRepository(db_client)

        servers = await ExtendedMCPServer.find_all().to_list()
        stats["total"] = len(servers)
        logger.info("Found %d MCP servers", stats["total"])

        for server in servers:
            server_id = str(server.id)
            disabled = list(server.registryDisabledTools or [])
            if dry_run:
                logger.info(
                    "[dry-run] would set tool_enabled=True for server_id=%s (then False for %d disabled tools)",
                    server_id,
                    len(disabled),
                )
                continue
            # Blanket-enable every doc, then re-disable this server's own disabled tools so the
            # backfill stays correct if re-run after tools have already been disabled via PATCH.
            result = await mcp_repo.update_entity_metadata("server_id", server_id, {"tool_enabled": True})
            if not result.error and disabled:
                result = await mcp_repo.update_tools_metadata(server_id, disabled, {"tool_enabled": False})
            if result.error:
                stats["failed"] += 1
                logger.error("Failed to patch server_id=%s: %s", server_id, result.error)
            else:
                stats["patched"] += 1
                logger.info("Patched server_id=%s (re-disabled %d tools)", server_id, len(disabled))
    finally:
        db_client.close()
        await close_mongodb()

    return stats


async def main(dry_run: bool = False) -> None:
    logger.info("=" * 80)
    logger.info("weaviate tool_enabled Backfill Script (AS-1861)")
    logger.info("=" * 80)
    if dry_run:
        logger.info("Running in DRY RUN mode - no changes will be made")

    try:
        stats = await backfill_tool_enabled(dry_run=dry_run)
    except Exception as e:
        logger.error("Backfill failed with error: %s", e, exc_info=True)
        sys.exit(1)

    logger.info("\n" + "=" * 80)
    logger.info("Backfill Summary")
    logger.info("=" * 80)
    logger.info("Total servers found: %d", stats["total"])
    logger.info("Servers patched:     %d", stats["patched"])
    logger.info("Servers failed:      %d", stats["failed"])

    if stats["failed"] > 0:
        logger.error("\nBackfill completed with failures. Re-run to retry the failed servers.")
        sys.exit(1)

    if dry_run:
        logger.info("\nDRY RUN completed. Run without --dry-run to apply changes.")
    else:
        logger.info("\nBackfill completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill tool_enabled=True on all MCP_Servers weaviate docs")
    parser.add_argument("--dry-run", action="store_true", help="Report pending updates without making changes")
    parser.add_argument("--force", action="store_true", help="Skip interactive confirmation")
    args = parser.parse_args()

    if not args.dry_run and not args.force:
        print("\n" + "=" * 80)
        print("WARNING: This migration will modify your weaviate index!")
        print("=" * 80)
        print("This script sets tool_enabled=True on every existing MCP_Servers doc.")
        print("Run it at or before the deploy that ships the discover_servers tool_enabled filter.")
        print("Recommendation: run with --dry-run first.")
        print("=" * 80)

        response = input("\nDo you want to continue? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("Migration cancelled.")
            sys.exit(0)

    asyncio.run(main(dry_run=args.dry_run))
