"""
Vector DB Sync Script for MCP Gateway Registry

Synchronizes MCP servers and/or A2A agents from MongoDB to the vector database for semantic search.
Each entity is fully rebuilt (delete existing docs + reinsert), so the script is safe to re-run
and will correct any stale or missing data.

Reads connection settings from .env at the project root.

Usage:
    uv run python scripts/vector_sync.py --target <mcp|a2a|all> [options]

Required:
    --target mcp|a2a|all  Which entities to sync (required)

Options:
    --clean         Delete the target collection(s) before syncing
    --batch-size N  Entities to process per MongoDB page (default: 100)

Examples:
    uv run python scripts/vector_sync.py --target mcp
    uv run python scripts/vector_sync.py --target a2a --clean
    uv run python scripts/vector_sync.py --target all --batch-size 50
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
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.vector.client import create_database_client

VALID_TARGETS = {"mcp", "a2a", "all"}


class SyncStats:
    """Statistics tracker for one entity type."""

    def __init__(self, label: str):
        self.label = label
        self.total = 0
        self.synced = 0
        self.failed = 0
        self.entities_processed: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def record(self, name: str, path: str, indexed: int, failed: bool) -> None:
        self.entities_processed.append({"name": name, "path": path, "indexed": indexed, "failed": failed})

    def add_error(self, error: str) -> None:
        self.errors.append(error)

    def print_summary(self) -> None:
        label = self.label.upper()
        print(f"\n{'=' * 80}")
        print(f"SYNC SUMMARY — {label}")
        print(f"{'=' * 80}")
        print(f"Total:             {self.total}")
        print(f"Successfully synced: {self.synced} ✓")
        print(f"Failed:            {self.failed} ✗")

        if self.entities_processed:
            print(f"\n{'-' * 80}")
            print("PER-ENTITY BREAKDOWN:")
            print(f"{'-' * 80}")
            for e in self.entities_processed:
                status = "✓" if not e["failed"] else "✗"
                note = f"indexed={e['indexed']}" if e["indexed"] > 0 else "no content"
                print(f"  {status} {e['name']:<35} ({e['path']:<25}) {note}")

        if self.errors:
            print(f"\n{'-' * 80}")
            print(f"ERRORS ({len(self.errors)}):")
            print(f"{'-' * 80}")
            for err in self.errors:
                print(f"  ✗ {err}")

        print(f"{'=' * 80}")
        if self.failed == 0 and self.total > 0:
            print(f"✓ {label} SUCCESS: all {self.total} entities synced.")
        elif self.synced > 0:
            pct = self.synced / self.total * 100
            print(f"⚠ {label} PARTIAL: {self.synced}/{self.total} synced ({pct:.1f}%)")
        elif self.total == 0:
            print(f"○ {label}: nothing found in MongoDB.")
        else:
            print(f"✗ {label} FAILED: no entities were synced.")
        print(f"{'=' * 80}\n")


def parse_args() -> tuple[str, bool, int]:
    target = None
    clean_mode = "--clean" in sys.argv
    batch_size = 100

    i = 0
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--target":
            if i + 1 >= len(sys.argv):
                print("Error: --target requires a value (mcp, a2a, or all)")
                sys.exit(1)
            target = sys.argv[i + 1].lower()
            if target not in VALID_TARGETS:
                print(f"Error: --target must be one of: {', '.join(sorted(VALID_TARGETS))}")
                sys.exit(1)
            i += 2
            continue
        if arg == "--batch-size":
            if i + 1 >= len(sys.argv):
                print("Error: --batch-size requires a value")
                sys.exit(1)
            try:
                batch_size = int(sys.argv[i + 1])
                if batch_size < 1:
                    print("Error: --batch-size must be at least 1")
                    sys.exit(1)
            except ValueError:
                print(f"Error: invalid --batch-size value: {sys.argv[i + 1]}")
                sys.exit(1)
            i += 2
            continue
        i += 1

    if target is None:
        print("Error: --target is required. Choose one of: mcp, a2a, all")
        print(f"\nUsage: uv run python {sys.argv[0]} --target <mcp|a2a|all> [--clean] [--batch-size N]")
        sys.exit(1)

    return target, clean_mode, batch_size


async def clean_collection(repo: Any, collection_name: str, label: str) -> None:
    print(f"Cleaning {label} collection ({collection_name})...")
    try:
        deleted = await repo.adelete_by_filter(filters={"collection": collection_name})
        print(f"  ✓ Deleted {deleted} documents\n")
    except Exception as e:
        print(f"  ✗ Error cleaning {label}: {e}")
        traceback.print_exc()


async def _sync_one_server(server: Any, stats: SyncStats, repo: Any) -> None:
    name = server.serverName
    path = server.path or ""
    server_id = str(server.id)

    print(f"  Syncing: {name} ({path}) [id={server_id}]")
    try:
        result = await repo.sync_to_vector_db(server, is_delete=True)
        _record_result(stats, name, path, result, indexed_key="indexed_tools", failed_key="failed_tools")
    except Exception as e:
        _record_exception(stats, name, path, e)


async def sync_all_servers(
    server_service: Any,
    mcp_server_repo: Any,
    batch_size: int,
) -> SyncStats:
    stats = SyncStats("MCP Servers")
    _print_batch_header("MCP Servers", batch_size)

    try:
        _, total = await server_service.list_servers(page=1, per_page=1)
        print(f"Found {total} MCP servers in MongoDB\n")

        if total == 0:
            return stats

        total_pages = (total + batch_size - 1) // batch_size
        processed = 0

        for page in range(1, total_pages + 1):
            _print_batch_progress(page, total_pages, processed, min(processed + batch_size, total))
            servers, _ = await server_service.list_servers(page=page, per_page=batch_size)
            if not servers:
                break
            for server in servers:
                processed += 1
                stats.total = processed
                print(f"\n[{processed}/{total}]", end=" ")
                await _sync_one_server(server, stats, mcp_server_repo)
            print(f"\nBatch {page} done — {processed}/{total} ({processed / total * 100:.1f}%)")

    except Exception as e:
        print(f"\n✗ Fatal error during MCP sync: {e}")
        traceback.print_exc()
        stats.add_error(f"Fatal: {e}")

    return stats


async def _sync_one_agent(agent: Any, stats: SyncStats, repo: Any) -> None:
    name = agent.card.name
    path = agent.path or ""
    agent_id = str(agent.id)

    print(f"  Syncing: {name} ({path}) [id={agent_id}]")
    try:
        result = await repo.sync_to_vector_db(agent, is_delete=True)
        _record_result(stats, name, path, result, indexed_key="indexed", failed_key="failed")
    except Exception as e:
        _record_exception(stats, name, path, e)


async def sync_all_agents(
    a2a_agent_service: Any,
    a2a_agent_repo: Any,
    batch_size: int,
) -> SyncStats:
    stats = SyncStats("A2A Agents")
    _print_batch_header("A2A Agents", batch_size)

    try:
        _, total = await a2a_agent_service.list_agents(page=1, per_page=1)
        print(f"Found {total} A2A agents in MongoDB\n")

        if total == 0:
            return stats

        total_pages = (total + batch_size - 1) // batch_size
        processed = 0

        for page in range(1, total_pages + 1):
            _print_batch_progress(page, total_pages, processed, min(processed + batch_size, total))
            agents, _ = await a2a_agent_service.list_agents(page=page, per_page=batch_size)
            if not agents:
                break
            for agent in agents:
                processed += 1
                stats.total = processed
                print(f"\n[{processed}/{total}]", end=" ")
                await _sync_one_agent(agent, stats, a2a_agent_repo)
            print(f"\nBatch {page} done — {processed}/{total} ({processed / total * 100:.1f}%)")

    except Exception as e:
        print(f"\n✗ Fatal error during A2A sync: {e}")
        traceback.print_exc()
        stats.add_error(f"Fatal: {e}")

    return stats


def _record_result(
    stats: SyncStats,
    name: str,
    path: str,
    result: dict,
    indexed_key: str,
    failed_key: str,
) -> None:
    indexed = result.get(indexed_key, 0)
    failed = result.get(failed_key, 0)
    error = result.get("error")

    if error or failed:
        msg = f"{name}: {failed_key}={failed} error={error}"
        print(f"    ✗ {msg}")
        stats.add_error(msg)
        stats.failed += 1
        stats.record(name, path, 0, failed=True)
    elif indexed == 0:
        print("    ○ no content to index")
        stats.synced += 1
        stats.record(name, path, 0, failed=False)
    else:
        print(f"    ✓ indexed {indexed} doc(s)")
        stats.synced += 1
        stats.record(name, path, indexed, failed=False)


def _record_exception(stats: SyncStats, name: str, path: str, exc: Exception) -> None:
    msg = f"{name}: unexpected error — {exc}"
    print(f"    ✗ {msg}")
    stats.add_error(msg)
    stats.failed += 1
    stats.record(name, path, 0, failed=True)


def _print_batch_header(label: str, batch_size: int) -> None:
    print(f"\n{'=' * 80}")
    print(f"MONGODB → VECTOR DB SYNC  ({label})")
    print(f"{'=' * 80}")
    print(f"Started:    {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Batch size: {batch_size}")
    print(f"{'=' * 80}")


def _print_batch_progress(page: int, total_pages: int, processed: int, end: int) -> None:
    print(f"\n{'─' * 60}")
    print(f"Batch {page}/{total_pages}  (entities {processed + 1}–{end})")
    print(f"{'─' * 60}")


async def run() -> int:
    target, clean_mode, batch_size = parse_args()

    env_path = Path(".env")
    if not env_path.exists():
        print(f"Error: .env not found at {env_path.resolve()}")
        return 1

    settings = Settings(_env_file=str(env_path))

    db_client = None
    redis_client = None
    container = None

    try:
        await init_mongodb(settings.mongo_config)
        print("✓ MongoDB connected")

        redis_client = create_redis_client(settings.redis_config)
        print("✓ Redis connected")

        db_client = create_database_client(settings.vector_backend_config)
        print(f"✓ Vector DB connected  (store={settings.vector_store_type}, provider={settings.embedding_provider})\n")

        container = RegistryContainer(
            settings=settings,
            db_client=db_client,
            redis_client=redis_client,
        )

        sync_mcp = target in {"mcp", "all"}
        sync_a2a = target in {"a2a", "all"}

        print(f"Target:     {target}")
        print(f"Mode:       {'CLEAN + SYNC' if clean_mode else 'SYNC (full rebuild per entity)'}")
        print(f"Batch size: {batch_size}\n")

        if clean_mode:
            if sync_mcp:
                await clean_collection(
                    container.mcp_server_repo,
                    ExtendedMCPServer.COLLECTION_NAME,
                    "MCP Servers",
                )
            if sync_a2a:
                await clean_collection(
                    container.a2a_agent_repo,
                    A2AAgent.COLLECTION_NAME,
                    "A2A Agents",
                )

        all_failed = 0

        if sync_mcp:
            mcp_stats = await sync_all_servers(
                server_service=container.server_service,
                mcp_server_repo=container.mcp_server_repo,
                batch_size=batch_size,
            )
            mcp_stats.print_summary()
            all_failed += mcp_stats.failed

        if sync_a2a:
            a2a_stats = await sync_all_agents(
                a2a_agent_service=container.a2a_agent_service,
                a2a_agent_repo=container.a2a_agent_repo,
                batch_size=batch_size,
            )
            a2a_stats.print_summary()
            all_failed += a2a_stats.failed

        return 1 if all_failed > 0 else 0

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
        print("Connections closed.")


def cli() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    cli()
