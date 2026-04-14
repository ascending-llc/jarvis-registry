"""
MongoDB to Weaviate Sync Script for MCP Gateway Registry

This script synchronizes MCP servers from MongoDB to Weaviate.
It reads all servers from MongoDB using the DI-managed server service and imports
them into Weaviate for semantic search. Already existing servers are skipped.

Usage:
    uv run python scripts/sync_mongo_to_weaviate.py [--clean] [--batch-size N] [--env-file PATH]

Options:
    --clean: Delete all existing servers from Weaviate before syncing
    --batch-size N: Number of servers to process per batch (default: 100)
    --env-file PATH: Explicit path to .env file to load (default: .env)
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from registry.container import RegistryContainer
from registry.core.config import Settings
from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.database.redis_client import close_redis_client, create_redis_client
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.vector.client import create_database_client


class SyncStats:
    """Statistics tracker for sync operation."""

    def __init__(self):
        self.total_servers = 0
        self.servers_with_tools = 0
        self.servers_without_tools = 0
        self.total_tools = 0
        self.tools_imported = 0
        self.tools_skipped = 0
        self.tools_failed = 0
        self.servers_processed: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def add_server(
        self,
        server_name: str,
        server_path: str,
        tool_count: int,
        imported: int,
        skipped: int,
        failed: int,
    ):
        """Record server processing stats."""
        self.servers_processed.append(
            {
                "name": server_name,
                "path": server_path,
                "total_tools": tool_count,
                "imported": imported,
                "skipped": skipped,
                "failed": failed,
            }
        )

    def add_error(self, error: str):
        """Record an error."""
        self.errors.append(error)

    def print_summary(self):
        """Print comprehensive summary."""
        print("\n" + "=" * 80)
        print("SYNC SUMMARY")
        print("=" * 80)
        print(f"Total Servers Scanned:      {self.total_servers}")
        print(f"Servers Imported:           {self.servers_with_tools}")
        print(f"Servers Skipped:            {self.servers_without_tools}")
        print(f"\nTotal Servers:              {self.total_tools}")
        print(f"Servers Imported:           {self.tools_imported} ✓")
        print(f"Servers Skipped (existing): {self.tools_skipped}")
        print(f"Servers Failed:             {self.tools_failed} ✗")

        if self.servers_processed:
            print(f"\n{'-' * 80}")
            print("PER-SERVER BREAKDOWN:")
            print(f"{'-' * 80}")
            for server in self.servers_processed:
                status = "✓" if server["imported"] > 0 else "○"
                print(f"{status} {server['name']:<30} ({server['path']:<20})")
                print(
                    f"  Imported: {server['imported']:>3} | "
                    f"Skipped: {server['skipped']:>3} | "
                    f"Failed: {server['failed']:>3}"
                )

        if self.errors:
            print(f"\n{'-' * 80}")
            print(f"ERRORS ({len(self.errors)}):")
            print(f"{'-' * 80}")
            for error in self.errors:
                print(f"✗ {error}")

        print("=" * 80)

        success_rate = (self.tools_imported / self.total_tools * 100) if self.total_tools > 0 else 0
        if self.tools_failed == 0 and self.total_tools > 0:
            print(f"✓ SUCCESS: All {self.total_tools} servers processed successfully!")
        elif self.tools_imported > 0:
            print(f"⚠ PARTIAL SUCCESS: {self.tools_imported}/{self.total_tools} servers imported ({success_rate:.1f}%)")
        else:
            print("✗ FAILED: No servers were imported")
        print("=" * 80 + "\n")


def parse_args() -> tuple[bool, int, str]:
    """Parse command line arguments."""
    clean_mode = "--clean" in sys.argv

    batch_size = 100
    env_file = ".env"

    i = 0
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg == "--batch-size":
            if i + 1 >= len(sys.argv):
                print("Error: --batch-size requires a value")
                sys.exit(1)
            try:
                batch_size = int(sys.argv[i + 1])
                if batch_size < 1:
                    print("Error: batch-size must be at least 1")
                    sys.exit(1)
            except ValueError:
                print(f"Error: Invalid batch-size value: {sys.argv[i + 1]}")
                sys.exit(1)
            i += 2
            continue

        if arg == "--env-file":
            if i + 1 >= len(sys.argv):
                print("Error: --env-file requires a value")
                sys.exit(1)
            env_file = sys.argv[i + 1]
            i += 2
            continue

        i += 1

    return clean_mode, batch_size, env_file


async def check_server_exists(mcp_server_repo, server_id: str) -> bool:
    """Check if server already exists in Weaviate."""
    try:
        existing = await mcp_server_repo.get_by_server_id(server_id)
        return existing is not None
    except Exception as e:
        print(f"  Warning: Failed to check existence for server {server_id}: {e}")
        return False


async def sync_server(server: Any, stats: SyncStats, mcp_server_repo):
    """Sync a single server to Weaviate."""
    server_name = server.serverName
    server_path = server.path
    server_id = str(server.id)

    print(f"Processing: {server_name} ({server_path}) [ID: {server_id}]")

    try:
        exists = await check_server_exists(mcp_server_repo, server_id)

        if exists:
            print("  ○ Server already exists, skipping...")
            stats.servers_without_tools += 1
            stats.tools_skipped += 1
            stats.add_server(server_name, server_path, 1, 0, 1, 0)
            return

        num_tools = server.numTools if hasattr(server, "numTools") else 0
        print(f"  Server has {num_tools} tools")
        stats.servers_with_tools += 1
        stats.total_tools += 1

        result = await mcp_server_repo.sync_server_to_vector_db(
            server=server,
            is_delete=False,
        )

        if result and result.get("indexed_tools", 0) > 0:
            print("  ✓ Successfully imported server")
            stats.tools_imported += 1
            stats.add_server(server_name, server_path, 1, 1, 0, 0)
        else:
            error_msg = f"{server_name}: Failed to import server"
            print(f"  ✗ {error_msg}")
            stats.add_error(error_msg)
            stats.tools_failed += 1
            stats.add_server(server_name, server_path, 1, 0, 0, 1)

    except Exception as e:
        error_msg = f"Unexpected error processing {server_name}: {e}"
        print(f"  ✗ {error_msg}")
        stats.add_error(error_msg)
        stats.total_tools += 1
        stats.tools_failed += 1
        stats.add_server(server_name, server_path, 1, 0, 0, 1)


async def clean_weaviate(mcp_server_repo):
    """Delete all existing servers from Weaviate using DI-managed repository."""
    print("Deleting all existing servers...")
    try:
        deleted = await mcp_server_repo.adelete_by_filter(filters={"collection": ExtendedMCPServer.COLLECTION_NAME})
        print(f"✓ Deleted {deleted} servers from Weaviate")
        print("=" * 80 + "\n")
        return deleted
    except Exception as e:
        print(f"✗ Error cleaning Weaviate: {e}")
        traceback.print_exc()
        return 0


async def sync_all_servers(server_service, mcp_server_repo, batch_size: int = 100):
    stats = SyncStats()
    print("\n" + "=" * 80)
    print("MONGODB TO WEAVIATE SYNC")
    print("=" * 80)
    print(f"Started at: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Batch size: {batch_size} servers per batch")
    print("=" * 80)

    try:
        print("\nFetching server count from MongoDB...")
        _, total = await server_service.list_servers(page=1, per_page=1)

        print(f"Found {total} servers in MongoDB")

        if total == 0:
            print("\n  No servers found in MongoDB. Nothing to sync.")
            return stats

        total_pages = (total + batch_size - 1) // batch_size
        print(f"Will process in {total_pages} batch(es)\n")

        processed_count = 0

        for page in range(1, total_pages + 1):
            print(f"\n{'─' * 80}")
            print(
                f"BATCH {page}/{total_pages} (Servers {processed_count + 1}-{min(processed_count + batch_size, total)})"
            )
            print(f"{'─' * 80}")

            servers, _ = await server_service.list_servers(page=page, per_page=batch_size)

            if not servers:
                print(f"  No servers returned for page {page}, stopping...")
                break

            for server in servers:
                processed_count += 1
                stats.total_servers = processed_count

                print(f"\n[{processed_count}/{total}] ", end="")
                await sync_server(server, stats, mcp_server_repo)

            print(f"\n{'─' * 80}")
            print(
                f"Batch {page} completed. Progress: "
                f"{processed_count}/{total} servers ({processed_count / total * 100:.1f}%)"
            )
            print(f"{'─' * 80}")

        return stats

    except Exception as e:
        print(f"\n✗ Fatal error during sync: {e}")
        traceback.print_exc()
        stats.add_error(f"Fatal error: {e}")
        return stats


async def run() -> int:
    """Main async runner."""
    clean_mode, batch_size, env_file = parse_args()

    env_path = Path(env_file)
    if not env_path.exists():
        print(f"Error: env file not found: {env_path}")
        return 1

    settings = Settings(_env_file=str(env_path))
    settings.configure_logging()

    db_client = None
    redis_client = None
    container = None

    try:
        await init_mongodb(settings.mongo_config)
        print("✓ Connected to MongoDB successfully!")

        redis_client = create_redis_client(settings.redis_config)
        print("✓ Connected to Redis successfully!")

        db_client = create_database_client(settings.vector_backend_config)
        print("✓ Connected to vector database successfully!")
        print(f"✓ Active vector backend: store={settings.vector_store_type}, provider={settings.embedding_provider}\n")

        container = RegistryContainer(
            settings=settings,
            db_client=db_client,
            redis_client=redis_client,
        )

        server_service = container.server_service
        mcp_server_repo = container.mcp_server_repo

        print(f"Mode: {'CLEAN + SYNC' if clean_mode else 'SYNC ONLY'}")
        print(f"Batch size: {batch_size}\n")

        if clean_mode:
            await clean_weaviate(mcp_server_repo)

        stats = await sync_all_servers(
            server_service=server_service,
            mcp_server_repo=mcp_server_repo,
            batch_size=batch_size,
        )

        stats.print_summary()
        return 1 if stats.tools_failed > 0 else 0

    except Exception as e:
        print(f"\n✗ Fatal Error: {e}")
        traceback.print_exc()
        return 1

    finally:
        if container is not None:
            try:
                await container.shutdown()
            except Exception:
                traceback.print_exc()

        if redis_client is not None:
            try:
                close_redis_client(redis_client)
            except Exception:
                traceback.print_exc()

        if db_client is not None:
            try:
                db_client.close()
            except Exception:
                traceback.print_exc()

        try:
            await close_mongodb()
        except Exception:
            traceback.print_exc()

        print("\nConnections closed.")


def cli():
    """CLI entry point for script execution."""
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    cli()
