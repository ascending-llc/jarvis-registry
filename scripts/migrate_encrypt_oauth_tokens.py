#!/usr/bin/env python3
"""
Migration Script: Delete Unencrypted OAuth Tokens

This script deletes unencrypted OAuth access and refresh tokens.
Encrypted tokens have the format "iv_hex:ciphertext_hex" (contains colon separator).

Usage:
    uv run python scripts/migrate_encrypt_oauth_tokens.py
"""

import asyncio
import os

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import Token


async def delete_unencrypted_tokens():
    """Delete all unencrypted OAuth access and refresh tokens"""
    # Get MongoDB connection from environment
    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")
    db_name = mongo_uri.split("/")[-1].split("?")[0] if "/" in mongo_uri.split("://")[-1] else "jarvis"

    print(f"Connecting to MongoDB: {mongo_uri}")
    print(f"Database: {db_name}\n")

    # Create MongoDB config
    config = MongoConfig(
        mongo_uri=mongo_uri,
        mongodb_username=os.getenv("MONGODB_USERNAME", ""),
        mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
    )

    # Initialize MongoDB client
    await MongoDB.connect_db(config=config, db_name=db_name)
    print("Connected successfully\n")

    total_deleted = 0

    # Process access tokens
    print("Processing access tokens (mcp_oauth)...")
    access_tokens = await Token.find({"type": "mcp_oauth"}).to_list()
    access_unencrypted = [t for t in access_tokens if t.token and ":" not in t.token]

    for token in access_unencrypted:
        await token.delete()
        total_deleted += 1

    print(f"  Deleted: {len(access_unencrypted)} unencrypted")
    print(f"  Kept: {len(access_tokens) - len(access_unencrypted)} encrypted\n")

    # Process refresh tokens
    print("Processing refresh tokens (mcp_oauth_refresh)...")
    refresh_tokens = await Token.find({"type": "mcp_oauth_refresh"}).to_list()
    refresh_unencrypted = [t for t in refresh_tokens if t.token and ":" not in t.token]

    for token in refresh_unencrypted:
        await token.delete()
        total_deleted += 1

    print(f"  Deleted: {len(refresh_unencrypted)} unencrypted")
    print(f"  Kept: {len(refresh_tokens) - len(refresh_unencrypted)} encrypted\n")

    print(f"Total deleted: {total_deleted} tokens")

    # Close connection
    if MongoDB.client:
        await MongoDB.client.close()


async def main():
    """Main entry point"""
    print("=" * 60)
    print("OAuth Token Encryption Migration")
    print("=" * 60)
    print()

    try:
        await delete_unencrypted_tokens()
        print("\nMigration completed successfully!")
    except Exception as e:
        print(f"\nMigration failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
