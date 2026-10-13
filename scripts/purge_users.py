"""Manually purge explicitly named users from the shared database; dry-run by default.

Run from the repository root with ``uv run python scripts/purge_users.py --help``.
See scripts/README.md for backups and the required quiet window.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import ValidationError
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from registry_pkgs.core.config import MongoSettings
from registry_pkgs.database.mongodb import create_mongo_client
from registry_pkgs.models.model_gateway_selection import MODEL_GATEWAY_SELECTION_ID

logger = logging.getLogger(__name__)

type DocumentId = ObjectId | str

# Ordinary ownership references. ACLs have additional type/resource rules below.
CASCADE_MAP = {
    "transactions": "user",
    "conversations": "user",
    "messages": "user",
    "files": "user",  # MongoDB metadata only, not external file storage.
    "sharedlinks": "user",
    "keys": "userId",
    "tokens": "userId",
    "agents": "author",  # Chat agents; NOT registry a2a_agents.
    "mcpservers": "author",
    "aclentries": "principalId",
    "users": "_id",
}
RESOURCE_TYPES = {"agents": {"agent", "remoteAgent"}, "mcpservers": {"mcpServer"}}
DELETE_ORDER = (
    "transactions",
    "conversations",
    "messages",
    "files",
    "sharedlinks",
    "keys",
    "tokens",
    "aclentries",
    "agents",
    "mcpservers",
    "users",
)
FIELD_SAMPLE_SIZE = 500
DELETE_BATCH_SIZE = 500


class SafetyError(ValueError):
    """An operator must resolve a failed precondition before retrying."""


@dataclass
class PurgePlan:
    targets: tuple[ObjectId, ...]
    deletions: dict[str, frozenset[DocumentId]] = field(default_factory=dict)
    # Keep only the ownership/name fields needed to detect a changed plan and delete vectors.
    resources: dict[str, dict[DocumentId, dict[str, Any]]] = field(default_factory=dict)
    blocked_acls: set[DocumentId] = field(default_factory=set)
    unmapped: dict[tuple[str, str], frozenset[DocumentId]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(len(ids) for ids in self.deletions.values())


def normalize_user_ids(values: list[str]) -> tuple[ObjectId, ...]:
    try:
        return tuple(sorted({ObjectId(value) for value in values}))
    except InvalidId as exc:
        raise ValueError("Each --user-id must be a 24-character hexadecimal ObjectId.") from exc


def id_forms(ids: Iterable[DocumentId]) -> list[DocumentId]:
    """Expand references without changing actual document _id values used for deletion."""
    forms: set[DocumentId] = set()
    for value in ids:
        forms.add(value)
        forms.add(str(value))
        if ObjectId.is_valid(value):
            forms.add(ObjectId(value))
    return sorted(forms, key=lambda value: (str(value), type(value).__name__))


async def find_ids(db: AsyncDatabase, collection: str, query: dict[str, Any]) -> frozenset[DocumentId]:
    return frozenset([doc["_id"] async for doc in db[collection].find(query, {"_id": 1})])


async def build_plan(db: AsyncDatabase, targets: tuple[ObjectId, ...]) -> PurgePlan:
    plan = PurgePlan(targets)
    references = id_forms(targets)
    for collection, owner_field in CASCADE_MAP.items():
        query: dict[str, Any] = {owner_field: {"$in": references}}
        if collection == "aclentries":
            query["principalType"] = "user"
        plan.deletions[collection] = await find_ids(db, collection, query)
        if collection in RESOURCE_TYPES:
            plan.resources[collection] = {
                doc["_id"]: doc async for doc in db[collection].find(query, {"_id": 1, "author": 1, "serverName": 1})
            }

    resource_acls: set[DocumentId] = set()
    for collection, resources in plan.resources.items():
        if not resources:
            continue
        async for acl in db["aclentries"].find(
            {"resourceId": {"$in": id_forms(resources)}},
            {"_id": 1, "principalType": 1, "principalId": 1, "resourceType": 1},
        ):
            # Unknown historical resource types also fail closed, including own-user grants.
            if (
                acl.get("resourceType") not in RESOURCE_TYPES[collection]
                or acl.get("principalType") != "user"
                or acl.get("principalId") not in references
            ):
                plan.blocked_acls.add(acl["_id"])
            else:
                resource_acls.add(acl["_id"])
    plan.deletions["aclentries"] |= resource_acls

    # Sampling discovers field names; reference queries cover the ENTIRE collection.
    for collection in sorted(await db.list_collection_names()):
        fields: set[str] = set()
        async for doc in db[collection].find({}).limit(FIELD_SAMPLE_SIZE):
            fields.update(doc)
        for name in sorted(fields):
            if (collection, name) == ("aclentries", "grantedBy"):
                continue  # Audit-only reference, intentionally retained.
            if name == CASCADE_MAP.get(collection) and collection != "aclentries":
                continue
            query = {name: {"$in": references}}
            if (collection, name) == ("aclentries", "principalId"):
                # A reference of another/unknown principal type is not covered by our cascade.
                query["principalType"] = {"$ne": "user"}
            matches = await find_ids(db, collection, query)
            if matches:
                plan.unmapped[collection, name] = matches
    return plan


def print_plan(plan: PurgePlan, *, skip_vector: bool) -> None:
    print("Targets: " + ", ".join(map(str, plan.targets)))
    for collection in DELETE_ORDER:
        print(f"{collection}: planned={len(plan.deletions[collection])}")
    print(f"TOTAL: planned={plan.total}")
    servers = plan.resources["mcpservers"]
    print(f"Weaviate: {'SKIPPED; retained vectors for' if skip_vector else 'will clean'} {len(servers)} server(s)")
    for server_id in sorted(servers, key=str):
        print(f"  server_id={server_id}")
    for acl_id in sorted(plan.blocked_acls, key=str):
        print(f"BLOCKED: foreign principal or unexpected resource type on aclentries _id={acl_id}")
    for (collection, name), ids in sorted(plan.unmapped.items()):
        print(f"UNMAPPED: {collection}.{name}: count={len(ids)} ids={','.join(sorted(map(str, ids)))}")


def check_plan(plan: PurgePlan, *, allow_unmapped: bool) -> None:
    if plan.blocked_acls:
        raise SafetyError("Authored resources have foreign or unrecognized ACLs. Resolve ownership before purging.")
    if plan.unmapped and not allow_unmapped:
        raise SafetyError("Unmapped references found. Review them before explicitly using --allow-unmapped.")
    if plan.unmapped:
        print("WARNING: --allow-unmapped leaves the reported references outside the cascade unmanaged.")


async def vector_generation(db: AsyncDatabase) -> str | None:
    active_job = await db["embedding_reindex_jobs"].find_one(
        {"status": "running", "leaseExpiresAt": {"$gt": datetime.now(UTC)}}, {"_id": 1}
    )
    if active_job:
        raise SafetyError("Embedding reindex is active. Retry after it completes.")
    selection = await db["model_gateway_selection"].find_one(
        {"_id": MODEL_GATEWAY_SELECTION_ID}, {"embeddingCollectionGeneration": 1}
    )
    generation = selection.get("embeddingCollectionGeneration") if selection else None
    if generation is not None and (not isinstance(generation, str) or not ObjectId.is_valid(generation)):
        raise SafetyError("The active embedding collection generation is invalid.")
    return generation


async def delete_vectors(db: AsyncDatabase, plan: PurgePlan) -> None:
    # Import and connect only AFTER all protections and operator confirmation have passed.
    from scripts._purge_user_vectors import VectorCleanupError, open_vector_repository

    generation = await vector_generation(db)
    try:
        async with open_vector_repository(generation) as repository:
            for server_id, server in plan.resources["mcpservers"].items():
                if await vector_generation(db) != generation:
                    raise SafetyError("Embedding generation changed. Stop writes and retry the purge.")
                deleted = await repository.delete_by_server_id(str(server_id), server.get("serverName"))
                print(f"Weaviate: server_id={server_id} deleted={deleted}")
            if await vector_generation(db) != generation:
                raise SafetyError("Embedding generation changed. MongoDB deletion has not started.")
    except VectorCleanupError as exc:
        # The helper deliberately strips SDK payloads; retain its useful, safe operator message.
        raise SafetyError(str(exc)) from exc


async def apply_plan(db: AsyncDatabase, plan: PurgePlan, *, skip_vector: bool, allow_unmapped: bool) -> None:
    fresh = await build_plan(db, plan.targets)
    check_plan(fresh, allow_unmapped=allow_unmapped)
    if fresh != plan:
        raise SafetyError("The deletion plan changed. Nothing was written; run dry-run again.")
    if plan.resources["mcpservers"] and not skip_vector:
        await delete_vectors(db, plan)
        # A vector operation may take time. Do not proceed with Mongo deletion after an ownership change.
        if await build_plan(db, plan.targets) != plan:
            raise SafetyError("The plan changed during vector cleanup. MongoDB deletion has not started.")
    for collection in DELETE_ORDER:
        ids = sorted(plan.deletions[collection], key=lambda value: (str(value), type(value).__name__))
        deleted = 0
        for offset in range(0, len(ids), DELETE_BATCH_SIZE):
            result = await db[collection].delete_many({"_id": {"$in": ids[offset : offset + DELETE_BATCH_SIZE]}})
            deleted += result.deleted_count
            # Print committed progress immediately, including before a subsequent batch can fail.
            print(f"{collection}: deleted={deleted} planned={len(ids)}")
        if not ids:
            print(f"{collection}: deleted=0 planned=0")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", action="append", required=True, help="User ObjectId; repeat for multiple users")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    mode.add_argument("--apply", action="store_true", help="Delete the planned records")
    parser.add_argument("--force", action="store_true", help="Skip confirmation; never bypass safety checks")
    parser.add_argument("--skip-vector", action="store_true", help="Leave MCP Server vectors untouched")
    parser.add_argument("--allow-unmapped", action="store_true", help="Proceed leaving reported unmapped references")
    args = parser.parse_args(argv)
    try:
        args.user_ids = normalize_user_ids(args.user_id)
    except ValueError as exc:
        parser.error(str(exc))
    return args


async def run_purge(db: AsyncDatabase, args: argparse.Namespace) -> None:
    plan = await build_plan(db, args.user_ids)
    print_plan(plan, skip_vector=args.skip_vector)
    check_plan(plan, allow_unmapped=args.allow_unmapped)
    if not args.apply:
        print("DRY RUN: no writes. Use --apply after reviewing the plan and taking a backup.")
        return
    if not plan.total:
        print("No matching records remain.")
        return
    if not args.force:
        answer = await asyncio.to_thread(input, "Delete this plan? Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            raise SafetyError("Cancelled; no writes.")
    await apply_plan(db, plan, skip_vector=args.skip_vector, allow_unmapped=args.allow_unmapped)
    print("Purge finished. Review any explicitly skipped vectors or unmapped references above.")


async def async_main(args: argparse.Namespace) -> None:
    settings = MongoSettings()
    client, database_name = create_mongo_client(settings.mongo_config)
    try:
        await client.admin.command("ping")
        print(f"MongoDB database: {database_name}")
        await run_purge(client[database_name], args)
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(async_main(args))
    except SafetyError as exc:
        print(f"STOPPED: {exc}")
        return 1
    except (PyMongoError, ValidationError, ValueError, RuntimeError, OSError, EOFError) as exc:
        # Driver errors and validation inputs can contain credentials. Report type, not raw payload.
        print(f"FAILED: {type(exc).__name__}; stopped at the last reported step. Review configuration and retry.")
        return 1
    except KeyboardInterrupt:
        print("Interrupted; review the last reported step and preview the same IDs before retrying.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
