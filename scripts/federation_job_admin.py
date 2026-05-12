"""
Federation job admin helper for inspecting and repairing federation sync state.

Usage:
    uv run federation-job-admin show <federation_id>
    uv run federation-job-admin list-active
    uv run federation-job-admin fail-active <federation_id>
    uv run federation-job-admin set-sync-state <federation_id> --status failed
    uv run federation-job-admin retry-vector-sync <federation_id>

Examples:
    uv run federation-job-admin show federation-demo-id
    uv run federation-job-admin list-active --limit 10
    uv run federation-job-admin fail-active federation-demo-id
    uv run federation-job-admin fail-active federation-demo-id --reason "manual recovery after restart"
    uv run federation-job-admin set-sync-state federation-demo-id --status failed --message "manual recovery"
    uv run federation-job-admin retry-vector-sync federation-demo-id
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus, urlsplit

from bson import ObjectId
from pymongo import DESCENDING, MongoClient

from registry.core.config import settings
from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.federation import Federation
from registry_pkgs.vector.client import create_database_client
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

ACTIVE_JOB_STATUSES = ("pending", "syncing")
SYNC_STATE_CHOICES = ["idle", "pending", "syncing", "success", "failed"]


def _json_default(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False))


def _get_db():
    config = settings.mongo_config
    parsed = urlsplit(config.mongo_uri)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise SystemExit("MongoDB database name is required in settings.mongo_uri")

    query_params = f"?{parsed.query}" if parsed.query else ""
    base_uri = f"{parsed.scheme}://{parsed.netloc}"
    if config.mongodb_username and config.mongodb_password:
        escaped_username = quote_plus(config.mongodb_username)
        escaped_password = quote_plus(config.mongodb_password)
        protocol, rest = base_uri.split("://", 1)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        mongo_uri = f"{protocol}://{escaped_username}:{escaped_password}@{rest}/{db_name}{query_params}"
    else:
        mongo_uri = f"{base_uri}/{db_name}{query_params}"

    client = MongoClient(mongo_uri)
    return client, client.get_default_database()


def _parse_object_id(raw_value: str) -> ObjectId:
    try:
        return ObjectId(raw_value)
    except Exception as exc:
        raise SystemExit(f"Invalid federation ObjectId: {raw_value}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect federation jobs, repair sync state, and rebuild vector indexes from Mongo state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run federation-job-admin show federation-demo-id\n"
            "  uv run federation-job-admin list-active --limit 10\n"
            "  uv run federation-job-admin fail-active federation-demo-id\n"
            '  uv run federation-job-admin fail-active federation-demo-id --reason "manual recovery"\n'
            "  uv run federation-job-admin set-sync-state federation-demo-id --status failed\n"
            "  uv run federation-job-admin retry-vector-sync federation-demo-id\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    show_parser = subparsers.add_parser("show", help="Show federation sync state and recent jobs")
    show_parser.add_argument("federation_id", help="Federation ObjectId")
    show_parser.add_argument("--limit", type=int, default=5, help="Number of recent jobs to print")

    list_active_parser = subparsers.add_parser("list-active", help="List active federation sync jobs")
    list_active_parser.add_argument("--limit", type=int, default=20, help="Maximum number of jobs to print")

    fail_active_parser = subparsers.add_parser(
        "fail-active",
        help="Mark the latest active job as failed and update federation sync state",
    )
    fail_active_parser.add_argument("federation_id", help="Federation ObjectId")
    fail_active_parser.add_argument(
        "--reason",
        default="manually cleared stuck job",
        help="Reason written into the failed job and federation sync message",
    )

    set_state_parser = subparsers.add_parser("set-sync-state", help="Update federation sync state directly")
    set_state_parser.add_argument("federation_id", help="Federation ObjectId")
    set_state_parser.add_argument(
        "--status",
        required=True,
        choices=SYNC_STATE_CHOICES,
        help="Target federation syncStatus",
    )
    set_state_parser.add_argument(
        "--message",
        default=None,
        help="Optional federation syncMessage",
    )

    retry_vector_parser = subparsers.add_parser(
        "retry-vector-sync",
        help="Rebuild Weaviate/vector index for the current federation Mongo state",
    )
    retry_vector_parser.add_argument("federation_id", help="Federation ObjectId")

    return parser


def _show_federation(db, federation_id: ObjectId, limit: int) -> None:
    federation = db.federations.find_one({"_id": federation_id})
    if federation is None:
        raise SystemExit(f"Federation not found: {federation_id}")

    jobs = list(
        db.federation_sync_jobs.find({"federationId": federation_id}).sort("createdAt", DESCENDING).limit(limit)
    )
    _print_json(
        {
            "federation": {
                "id": federation["_id"],
                "providerType": federation.get("providerType"),
                "displayName": federation.get("displayName"),
                "status": federation.get("status"),
                "syncStatus": federation.get("syncStatus"),
                "syncMessage": federation.get("syncMessage"),
                "lastSync": federation.get("lastSync"),
                "updatedAt": federation.get("updatedAt"),
            },
            "recentJobs": jobs,
        }
    )


def _list_active_jobs(db, limit: int) -> None:
    jobs = list(
        db.federation_sync_jobs.find({"status": {"$in": list(ACTIVE_JOB_STATUSES)}})
        .sort("createdAt", DESCENDING)
        .limit(limit)
    )
    _print_json({"count": len(jobs), "activeStatuses": list(ACTIVE_JOB_STATUSES), "jobs": jobs})


def _fail_active_job(db, federation_id: ObjectId, reason: str) -> None:
    now = datetime.now(UTC)
    active_job = db.federation_sync_jobs.find_one(
        {
            "federationId": federation_id,
            "status": {"$in": list(ACTIVE_JOB_STATUSES)},
        },
        sort=[("createdAt", DESCENDING)],
    )

    federation_result = db.federations.update_one(
        {"_id": federation_id},
        {
            "$set": {
                "syncStatus": "failed",
                "syncMessage": reason,
                "updatedAt": now,
            }
        },
    )
    if federation_result.matched_count == 0:
        raise SystemExit(f"Federation not found: {federation_id}")

    if active_job:
        db.federation_sync_jobs.update_one(
            {"_id": active_job["_id"]},
            {
                "$set": {
                    "status": "failed",
                    "phase": "failed",
                    "error": reason,
                    "finishedAt": now,
                    "updatedAt": now,
                }
            },
        )

    _print_json(
        {
            "federationId": federation_id,
            "activeJobId": active_job["_id"] if active_job else None,
            "clearedActiveJob": active_job is not None,
            "status": "failed",
            "message": reason,
        }
    )


def _set_sync_state(db, federation_id: ObjectId, status: str, message: str | None) -> None:
    now = datetime.now(UTC)
    result = db.federations.update_one(
        {"_id": federation_id},
        {
            "$set": {
                "syncStatus": status,
                "syncMessage": message,
                "updatedAt": now,
            }
        },
    )
    if result.matched_count == 0:
        raise SystemExit(f"Federation not found: {federation_id}")

    _print_json(
        {
            "federationId": federation_id,
            "syncStatus": status,
            "syncMessage": message,
        }
    )


async def _retry_vector_sync(federation_id: ObjectId) -> None:
    """Rebuild the federation vector index from the current persisted Mongo resources."""
    db_client = None

    try:
        await init_mongodb(settings.mongo_config)
        db_client = create_database_client(settings.vector_backend_config)
        mcp_repo = MCPServerRepository(db_client)
        a2a_repo = A2AAgentRepository(db_client)

        federation = await Federation.find_one({"_id": federation_id})
        if federation is None:
            raise SystemExit(f"Federation not found: {federation_id}")

        mcp_servers = await ExtendedMCPServer.find({"federationRefId": federation_id}).to_list()
        a2a_agents = await A2AAgent.find({"federationRefId": federation_id}).to_list()

        mcp_indexed = 0
        mcp_failed = 0
        mcp_skipped = 0
        a2a_indexed = 0
        a2a_failed = 0
        a2a_skipped = 0
        errors: list[str] = []

        for server in mcp_servers:
            try:
                result = await mcp_repo.sync_to_vector_db(server, is_delete=False)
                if not result or result.get("failed_tools"):
                    mcp_failed += 1
                    detail = result.get("error") if result else "sync returned no result"
                    errors.append(f"mcp:{server.serverName}:{detail}")
                    continue
                if result.get("skipped"):
                    mcp_skipped += int(result["skipped"])
                    continue
                mcp_indexed += 1
            except Exception as exc:
                mcp_failed += 1
                errors.append(f"mcp:{server.serverName}:{exc}")

        for agent in a2a_agents:
            try:
                result = await a2a_repo.sync_to_vector_db(agent, is_delete=False)
                if not result or result.get("failed"):
                    a2a_failed += 1
                    detail = result.get("error") if result else "sync returned no result"
                    errors.append(f"a2a:{agent.card.name}:{detail}")
                    continue
                if result.get("skipped"):
                    a2a_skipped += int(result["skipped"])
                    continue
                a2a_indexed += 1
            except Exception as exc:
                a2a_failed += 1
                errors.append(f"a2a:{agent.card.name}:{exc}")

        _print_json(
            {
                "federationId": federation_id,
                "displayName": federation.displayName,
                "scanned": {
                    "mcpServers": len(mcp_servers),
                    "a2aAgents": len(a2a_agents),
                },
                "result": {
                    "indexedMcpServers": mcp_indexed,
                    "failedMcpServers": mcp_failed,
                    "skippedMcpServers": mcp_skipped,
                    "indexedA2AAgents": a2a_indexed,
                    "failedA2AAgents": a2a_failed,
                    "skippedA2AAgents": a2a_skipped,
                },
                "firstError": errors[0] if errors else None,
                "errors": errors,
            }
        )
    finally:
        try:
            if db_client is not None:
                db_client.close()
        finally:
            await close_mongodb()


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        raise SystemExit(1)

    if args.command == "retry-vector-sync":
        asyncio.run(_retry_vector_sync(_parse_object_id(args.federation_id)))
        return

    client, db = _get_db()
    try:
        if args.command == "show":
            _show_federation(db, _parse_object_id(args.federation_id), args.limit)
            return

        if args.command == "list-active":
            _list_active_jobs(db, args.limit)
            return

        if args.command == "fail-active":
            _fail_active_job(db, _parse_object_id(args.federation_id), args.reason)
            return

        if args.command == "set-sync-state":
            _set_sync_state(db, _parse_object_id(args.federation_id), args.status, args.message)
            return

        raise SystemExit(f"Unsupported command: {args.command}")
    finally:
        client.close()


def cli() -> None:
    main()


if __name__ == "__main__":
    cli()
