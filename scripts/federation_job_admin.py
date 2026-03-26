"""
Federation job admin helper for inspecting and repairing federation sync state.

Usage:
    uv run federation-job-admin show <federation_id>
    uv run federation-job-admin list-active
    uv run federation-job-admin fail-active <federation_id>
    uv run federation-job-admin set-sync-state <federation_id> --status failed

Examples:
    uv run federation-job-admin show federation-demo-id
    uv run federation-job-admin list-active --limit 10
    uv run federation-job-admin fail-active federation-demo-id
    uv run federation-job-admin fail-active federation-demo-id --reason "manual recovery after restart"
    uv run federation-job-admin set-sync-state federation-demo-id --status failed --message "manual recovery"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING, MongoClient

from registry.core.config import settings

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
    client = MongoClient(settings.mongo_uri)
    return client, client.get_default_database()


def _parse_object_id(raw_value: str) -> ObjectId:
    try:
        return ObjectId(raw_value)
    except Exception as exc:
        raise SystemExit(f"Invalid federation ObjectId: {raw_value}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and repair federation sync jobs and federation sync state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run federation-job-admin show federation-demo-id\n"
            "  uv run federation-job-admin list-active --limit 10\n"
            "  uv run federation-job-admin fail-active federation-demo-id\n"
            '  uv run federation-job-admin fail-active federation-demo-id --reason "manual recovery"\n'
            "  uv run federation-job-admin set-sync-state federation-demo-id --status failed\n"
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


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        raise SystemExit(1)

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
