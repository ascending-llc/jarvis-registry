"""
Seed AccessRole data for ACL system - Federation roles only

Usage:
    python scripts/seed_access_roles_standalone.py
"""

import asyncio
import os
from datetime import UTC, datetime
from urllib.parse import quote_plus, urlsplit

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

# Load environment variables
load_dotenv()


async def seed_access_roles(collection):
    """
    Seed AccessRole records for federation resource type only.

    Uses direct MongoDB operations instead of Beanie ORM to avoid
    triggering A2AAgent model initialization.
    """
    print("=== Seeding Federation AccessRoles ===\n")

    # Only define federation roles - Chat handles all other resource types
    roles_data = [
        # Federation roles (Registry-specific)
        {
            "accessRoleId": "federation_viewer",
            "resourceType": "federation",
            "name": "com_ui_role_viewer",
            "description": "com_ui_role_viewer_desc",
            "permBits": 1,  # VIEW only
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "accessRoleId": "federation_editor",
            "resourceType": "federation",
            "name": "com_ui_role_editor",
            "description": "com_ui_role_editor_desc",
            "permBits": 3,  # VIEW + EDIT
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "accessRoleId": "federation_owner",
            "resourceType": "federation",
            "name": "com_ui_role_owner",
            "description": "com_ui_role_owner_desc",
            "permBits": 15,  # VIEW + EDIT + DELETE + SHARE
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
    ]

    created_count = 0
    updated_count = 0

    for role_data in roles_data:
        try:
            # Check if role exists using direct MongoDB query
            existing_role = await collection.find_one({"accessRoleId": role_data["accessRoleId"]})

            if existing_role:
                # Update existing role
                update_data = {
                    "resourceType": role_data["resourceType"],
                    "name": role_data["name"],
                    "description": role_data["description"],
                    "permBits": role_data["permBits"],
                    "updatedAt": datetime.now(UTC),
                }
                await collection.update_one({"accessRoleId": role_data["accessRoleId"]}, {"$set": update_data})
                updated_count += 1
                print(f"[OK] Updated: {role_data['accessRoleId']} (permBits={role_data['permBits']})")
            else:
                # Create new role
                await collection.insert_one(role_data)
                created_count += 1
                print(f"[OK] Created: {role_data['accessRoleId']} (permBits={role_data['permBits']})")
        except Exception as e:
            print(f"[ERROR] Error processing {role_data['accessRoleId']}: {e}")

    print("\n=== Summary ===")
    print(f"Created: {created_count} roles")
    print(f"Updated: {updated_count} roles")
    print(f"Total: {created_count + updated_count} roles\n")


async def main():
    """
    Main entry point - Direct MongoDB operations without Beanie

    This completely avoids importing any models, ensuring we don't
    trigger A2AAgent initialization.
    """
    # Get MongoDB connection details from environment
    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")
    mongo_username = os.getenv("MONGODB_USERNAME", "")
    mongo_password = os.getenv("MONGODB_PASSWORD", "")

    # Debug: Print raw env values
    print(f"Debug - Raw MONGO_URI from env: {mongo_uri}")
    print(f"Debug - Has username: {bool(mongo_username)}")
    print(f"Debug - Has password: {bool(mongo_password)}\n")

    # Parse database name from URI (same logic as MongoDB.connect_db)
    parsed = urlsplit(mongo_uri)
    path = parsed.path.lstrip("/")
    extracted_db = path if path else None
    db_name = extracted_db if extracted_db else "jarvis"

    if not db_name:
        raise ValueError("MongoDB database name is required in mongo_uri or explicit db_name")

    print("Connecting to MongoDB...")
    print(f"Database: {db_name}")
    print(f"Parsed scheme: {parsed.scheme}")
    print(f"Parsed netloc: {parsed.netloc}")
    print(f"Parsed path: {parsed.path}\n")

    # Build MongoDB URL with credentials (same logic as MongoDB.connect_db)
    query_params = f"?{parsed.query}" if parsed.query else ""
    base_uri = f"{parsed.scheme}://{parsed.netloc}"

    # Construct the final MongoDB URL
    if mongo_username and mongo_password:
        # Credentials provided via env vars - insert them into the URI
        escaped_username = quote_plus(mongo_username)
        escaped_password = quote_plus(mongo_password)
        protocol, rest = base_uri.split("://", 1)
        # Strip any existing credentials from rest (everything before @)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        mongodb_url = f"{protocol}://{escaped_username}:{escaped_password}@{rest}/{db_name}{query_params}"
    else:
        # Credentials already in URI or not needed
        mongodb_url = f"{base_uri}/{db_name}{query_params}" if db_name else base_uri

    # Debug: Print connection info (hide password)
    debug_url = mongodb_url
    if mongo_password:
        debug_url = mongodb_url.replace(mongo_password, "***")
    print(f"Debug - Connecting with URL: {debug_url}\n")

    # Create PyMongo async client
    client = AsyncMongoClient(
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
    )

    try:
        # Verify connection
        await client.admin.command("ping")
        print(f"Connected to MongoDB database: {db_name}\n")

        # Get database and collection directly (no Beanie/ORM)
        database = client[db_name]
        collection = database["accessroles"]  # Direct collection access

        # Seed roles using direct MongoDB operations
        await seed_access_roles(collection)
    finally:
        # Close MongoDB connection
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
