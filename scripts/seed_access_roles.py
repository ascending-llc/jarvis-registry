"""
Seed AccessRole data for ACL system - Federation roles only

Jarvis Chat initializes base roles (agent, mcpServer, promptGroup, etc).
Jarvis Registry only needs to seed the 3 federation roles.

Creates role definitions for federation resource type:
- federation_viewer
- federation_editor
- federation_owner

Usage:
    python scripts/seed_access_roles.py
"""

import asyncio
import os
from datetime import UTC, datetime

from beanie import init_beanie
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Load environment variables
load_dotenv()

# Import only the model we need, avoid triggering A2AAgent import
from registry_pkgs.models.extended_access_role import ExtendedAccessRole


async def seed_access_roles():
    """
    Seed AccessRole records for federation resource type only.

    Note: Jarvis Chat handles seeding of base roles (agent, mcpServer, promptGroup, etc).
    This script only needs to ensure the 3 federation roles exist.
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
        },
        {
            "accessRoleId": "federation_editor",
            "resourceType": "federation",
            "name": "com_ui_role_editor",
            "description": "com_ui_role_editor_desc",
            "permBits": 3,  # VIEW + EDIT
        },
        {
            "accessRoleId": "federation_owner",
            "resourceType": "federation",
            "name": "com_ui_role_owner",
            "description": "com_ui_role_owner_desc",
            "permBits": 15,  # VIEW + EDIT + DELETE + SHARE
        },
    ]

    created_count = 0
    updated_count = 0

    for role_data in roles_data:
        try:
            existing_role = await ExtendedAccessRole.find_one({"accessRoleId": role_data["accessRoleId"]})

            if existing_role:
                # Update existing role
                existing_role.resourceType = role_data["resourceType"]
                existing_role.name = role_data["name"]
                existing_role.description = role_data["description"]
                existing_role.permBits = role_data["permBits"]
                existing_role.updatedAt = datetime.now(UTC)
                await existing_role.save()
                updated_count += 1
                print(f"[OK] Updated: {role_data['accessRoleId']} (permBits={role_data['permBits']})")
            else:
                # Create new role
                now = datetime.now(UTC)
                role = ExtendedAccessRole(
                    accessRoleId=role_data["accessRoleId"],
                    resourceType=role_data["resourceType"],
                    name=role_data["name"],
                    description=role_data["description"],
                    permBits=role_data["permBits"],
                    createdAt=now,
                    updatedAt=now,
                )
                await role.insert()
                created_count += 1
                print(f"[OK] Created: {role_data['accessRoleId']} (permBits={role_data['permBits']})")
        except Exception as e:
            print(f"[ERROR] Error processing {role_data['accessRoleId']}: {e}")

    print("\n=== Summary ===")
    print(f"Created: {created_count} roles")
    print(f"Updated: {updated_count} roles")
    print(f"Total: {created_count + updated_count} roles\n")


async def main():
    """Main entry point"""
    # Get MongoDB URI from environment
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        raise ValueError("MONGODB_URI environment variable is required")

    # Initialize MongoDB connection with motor + beanie
    # Only initialize ExtendedAccessRole to avoid loading other models (e.g., A2AAgent)
    client = AsyncIOMotorClient(mongodb_uri)

    try:
        # Extract database name from URI or use default
        from urllib.parse import urlparse

        parsed = urlparse(mongodb_uri)
        db_name = parsed.path.lstrip("/") if parsed.path and parsed.path != "/" else "jarvis"

        database = client[db_name]

        # Initialize Beanie with only ExtendedAccessRole model
        await init_beanie(database=database, document_models=[ExtendedAccessRole])

        print(f"Connected to MongoDB database: {db_name}\n")

        # Seed roles
        await seed_access_roles()
    finally:
        # Close MongoDB connection
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
