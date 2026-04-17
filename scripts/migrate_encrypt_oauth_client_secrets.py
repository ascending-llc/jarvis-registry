#!/usr/bin/env python3
"""
Migration Script: Encrypt OAuth Client Secrets

This script encrypts unencrypted OAuth client_secret values in config.oauth.client_secret.
Previously, the encryption logic was incorrectly looking at config.authentication.client_secret,
resulting in plaintext secrets stored in the database.

The script is idempotent - it can be safely run multiple times.

Usage:
    uv run python scripts/migrate_encrypt_oauth_client_secrets.py
"""

import asyncio
import os
import re

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import encryption utilities
from registry.utils.crypto_utils import encrypt_value
from registry_pkgs.core.config import MongoConfig
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedMCPServer

# Encryption pattern for validation (32 hex chars followed by colon)
ENCRYPTED_PATTERN = re.compile(r"^[0-9a-f]{32}:")


def is_already_encrypted(value: str) -> bool:
    """Check if value is already encrypted"""
    return bool(ENCRYPTED_PATTERN.match(value))


async def encrypt_oauth_client_secrets():
    """Encrypt all unencrypted OAuth client secrets in mcpservers collection"""
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

    total_updated = 0
    total_already_encrypted = 0
    total_with_oauth = 0

    # Find all servers
    print("Processing mcpservers collection...")
    servers = await ExtendedMCPServer.find_all().to_list()
    print(f"Total servers: {len(servers)}\n")

    for server in servers:
        if not server.config:
            continue

        # Check if server has oauth config with client_secret
        oauth_config = server.config.get("oauth")
        if not oauth_config or not isinstance(oauth_config, dict):
            continue

        client_secret = oauth_config.get("client_secret")
        if not client_secret:
            continue

        total_with_oauth += 1

        # Check if already encrypted
        if is_already_encrypted(str(client_secret)):
            total_already_encrypted += 1
            print(f"  ✓ {server.serverName}: already encrypted")
            continue

        # Encrypt the client_secret
        try:
            encrypted_secret = encrypt_value(str(client_secret))
            server.config["oauth"]["client_secret"] = encrypted_secret
            await server.save()
            total_updated += 1
            print(f"  ✓ {server.serverName}: encrypted and saved")
        except Exception as e:
            print(f"  ✗ {server.serverName}: failed to encrypt - {e}")

    print("\nSummary:")
    print(f"  Total servers with OAuth: {total_with_oauth}")
    print(f"  Already encrypted: {total_already_encrypted}")
    print(f"  Newly encrypted: {total_updated}")

    # Close connection
    if MongoDB.client:
        await MongoDB.client.close()


async def main():
    """Main entry point"""
    print("=" * 60)
    print("OAuth Client Secret Encryption Migration")
    print("=" * 60)
    print()

    try:
        await encrypt_oauth_client_secrets()
        print("\nMigration completed successfully!")
    except Exception as e:
        print(f"\nMigration failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
