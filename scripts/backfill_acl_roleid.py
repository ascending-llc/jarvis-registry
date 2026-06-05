"""
Backfill roleId for historical ACL entries (Standalone version).
Usage:
    python scripts/backfill_acl_roleid.py
"""

import asyncio
import os
from urllib.parse import urlsplit

from pymongo import AsyncMongoClient


async def backfill_acl_roleids(db, session):
    """Fill null ``roleId`` on ACL entries by matching ``(resourceType, permBits)``."""
    print("=== Backfilling ACL roleId ===\n")

    access_roles = db["accessroles"]
    acl_entries = db["aclentries"]

    # Build (resourceType, permBits) -> roleId map across the whole catalog.
    role_map: dict[tuple[str, int], object] = {}
    async for role in access_roles.find({}, session=session):
        role_map[(role["resourceType"], role["permBits"])] = role["_id"]

    if not role_map:
        raise RuntimeError(
            "No roles found in 'accessroles'. Run seed_access_roles_standalone.py "
            "(and ensure Jarvis Chat has seeded base roles) first."
        )

    print(f"Loaded {len(role_map)} roles across {len({rt for rt, _ in role_map})} resource types\n")

    query = {"$or": [{"roleId": None}, {"roleId": {"$exists": False}}]}

    updated = 0
    skipped_by_type: dict[str, int] = {}
    async for entry in acl_entries.find(query, session=session):
        resource_type = entry.get("resourceType")
        perm_bits = entry.get("permBits")
        role_id = role_map.get((resource_type, perm_bits))
        if role_id is None:
            skipped_by_type[resource_type] = skipped_by_type.get(resource_type, 0) + 1
            continue

        await acl_entries.update_one({"_id": entry["_id"]}, {"$set": {"roleId": role_id}}, session=session)
        updated += 1
        print(f"[OK] entry {entry['_id']} ({resource_type}, permBits={perm_bits}) -> roleId={role_id}")

    print(f"\n=== Completed: {updated} updated ===")
    if skipped_by_type:
        print("Skipped (no matching role for resource type / permBits):")
        for rt, n in sorted(skipped_by_type.items()):
            print(f"  {rt}: {n}")
    print()


async def main():
    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")

    parsed = urlsplit(mongo_uri)
    path = parsed.path.lstrip("/")
    db_name = path if path else "jarvis"

    if not db_name:
        raise ValueError("MongoDB database name is required in MONGO_URI")

    query_params = f"?{parsed.query}" if parsed.query else ""
    mongodb_url = f"{parsed.scheme}://{parsed.netloc}/{db_name}{query_params}"

    async with AsyncMongoClient(
        mongodb_url,
        directConnection=True,
        maxPoolSize=50,
        minPoolSize=10,
        maxIdleTimeMS=30000,
        waitQueueTimeoutMS=5000,
        connectTimeoutMS=10000,
        serverSelectionTimeoutMS=10000,
        retryWrites=True,
        retryReads=True,
    ) as client:
        await client.admin.command("ping")
        print(f"Connected to MongoDB database: {db_name}\n")

        database = client[db_name]

        async with client.start_session() as session:

            async def callback(session):
                await backfill_acl_roleids(database, session)

            await session.with_transaction(callback)


if __name__ == "__main__":
    asyncio.run(main())
