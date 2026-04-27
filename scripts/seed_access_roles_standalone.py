"""
Seed AccessRole data for ACL system - Federation roles only (Standalone version)

This script directly operates on MongoDB collections without using Beanie ORM
to completely avoid any model imports that might trigger A2AAgent initialization.

Jarvis Chat initializes base roles (agent, mcpServer, promptGroup, etc).
Jarvis Registry only needs to seed the 3 federation roles.

Usage:
    python scripts/seed_access_roles_standalone.py
"""

import asyncio
import os
from datetime import UTC, datetime
from urllib.parse import urlsplit

from pymongo import AsyncMongoClient


async def seed_access_roles(collection, session):
    """
    Seed AccessRole records for federation resource type only.

    Uses direct MongoDB operations instead of Beanie ORM to avoid
    triggering A2AAgent model initialization.
    """
    print("=== Seeding Federation AccessRoles ===\n")

    # Only define federation roles - Chat handles all other resource types
    roles_data = [
        {
            "accessRoleId": "federation_viewer",
            "resourceType": "federation",
            "name": "com_ui_federation_role_viewer",
            "description": "com_ui_federation_viewer_desc",
            "permBits": 1,
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
            "__v": 0,
        },
        {
            "accessRoleId": "federation_editor",
            "resourceType": "federation",
            "name": "com_ui_federation_role_editor",
            "description": "com_ui_federation_editor_desc",
            "permBits": 3,
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
            "__v": 0,
        },
        {
            "accessRoleId": "federation_owner",
            "resourceType": "federation",
            "name": "com_ui_federation_role_owner",
            "description": "com_ui_federation_owner_desc",
            "permBits": 15,
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
            "__v": 0,
        },
    ]

    for role_data in roles_data:
        await collection.update_one(
            {"accessRoleId": role_data["accessRoleId"]}, {"$setOnInsert": role_data}, upsert=True, session=session
        )
        print(f"[OK] Ensured: {role_data['accessRoleId']} (permBits={role_data['permBits']})")

    print(f"\n=== Completed: {len(roles_data)} roles processed ===\n")


async def main():
    """
    Main entry point - Direct MongoDB operations without Beanie

    This completely avoids importing any models, ensuring we don't
    trigger A2AAgent initialization.
    """
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
        collection = database["accessroles"]

        async with client.start_session() as session:

            async def callback(session):
                await seed_access_roles(collection, session)

            await session.with_transaction(callback)


if __name__ == "__main__":
    asyncio.run(main())
