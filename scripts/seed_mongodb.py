"""
MongoDB Seed Script for MCP Gateway Registry

Seeds sample data for users, keys, tokens, and MCP servers.
Includes examples of servers using API keys and OAuth tokens.

Usage:
    python scripts/seed_mongodb.py          # Seed data (default)
    python scripts/seed_mongodb.py seed     # Seed data
    python scripts/seed_mongodb.py clean    # Clean all collections
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import PermissionBits, RoleBits

# Load environment variables from .env file
load_dotenv()

from registry.utils.crypto_utils import encrypt_auth_fields
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedAclEntry, ExtendedMCPServer, Group, Key, Token, User


async def seed_groups():
    """Seed sample groups based on scopes.yml group_mappings."""
    print("Seeding groups...")

    # Groups from scopes.yml group_mappings
    groups_data = [
        {
            "name": "jarvis-registry-admin",
            "description": "Full access to all registry resources",
            "email": "registry-admin@example.com",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "name": "jarvis-registry-power-user",
            "description": "Create/delete/share servers and agents",
            "email": "registry-power-user@example.com",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "name": "jarvis-registry-user",
            "description": "Create servers and agents, cannot share",
            "email": "registry-user@example.com",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "name": "jarvis-registry-read-only",
            "description": "Read-only access to servers and agents",
            "email": "registry-read-only@example.com",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
    ]

    created_groups = []
    for group_data in groups_data:
        # Check if group already exists
        existing_group = await Group.find_one(Group.name == group_data["name"])
        if existing_group:
            print(f"  Group {group_data['name']} already exists, skipping...")
            created_groups.append(existing_group)
        else:
            group = Group(**group_data)
            await group.insert()
            created_groups.append(group)
            print(f"  Created group: {group_data['name']}")

    return created_groups


async def seed_users():
    """Seed sample users."""
    print("Seeding users...")

    users_data = [
        {
            "name": "Admin User",
            "username": "admin",
            "email": "admin@example.com",
            "emailVerified": True,
            "password": "$2b$10$abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH",  # hashed password
            "role": "ADMIN",
            "provider": "local",
            "idOnTheSource": "entra-uid-1",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "name": "John Developer",
            "username": "johndoe",
            "email": "john.doe@example.com",
            "emailVerified": True,
            "password": "$2b$10$abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH",
            "role": "USER",
            "provider": "local",
            "idOnTheSource": "entra-uid-2",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "name": "Jane Smith",
            "username": "janesmith",
            "email": "jane.smith@example.com",
            "emailVerified": True,
            "password": "$2b$10$abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH",
            "role": "USER",
            "provider": "local",
            "idOnTheSource": "entra-uid-3",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "name": "Test User",
            "username": "testuser",
            "email": "test.user@example.com",
            "emailVerified": True,
            "password": "$2b$10$abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH",
            "role": "USER",
            "provider": "local",
            "idOnTheSource": "entra-uid-4",
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
    ]

    created_users = []
    for user_data in users_data:
        # Check if user already exists
        existing_user = await User.find_one(User.email == user_data["email"])
        if existing_user:
            print(f"  User {user_data['email']} already exists, skipping...")
            created_users.append(existing_user)
        else:
            user = User(**user_data)
            await user.insert()
            created_users.append(user)
            print(f"  Created user: {user_data['email']}")

    return created_users


async def seed_keys(users):
    """Seed API keys for users."""
    print("Seeding API keys...")

    keys_data = [
        {
            "userId": users[0].id,  # Admin user
            "name": "Admin API Key",
            "value": "sk_admin_1234567890abcdefghijklmnopqrstuvwxyz",
            "expiresAt": datetime.now(UTC) + timedelta(days=365),
        },
        {
            "userId": users[1].id,  # John Developer
            "name": "Development Key",
            "value": "sk_dev_abcdefghijklmnopqrstuvwxyz1234567890",
            "expiresAt": datetime.now(UTC) + timedelta(days=90),
        },
        {
            "userId": users[1].id,  # John Developer
            "name": "Testing Key",
            "value": "sk_test_xyz789012345678901234567890abcdefgh",
            "expiresAt": datetime.now(UTC) + timedelta(days=30),
        },
        {
            "userId": users[2].id,  # Jane Smith
            "name": "Production API Key",
            "value": "sk_prod_mnopqrstuvwxyz1234567890abcdefghijkl",
            "expiresAt": datetime.now(UTC) + timedelta(days=180),
        },
    ]

    created_keys = []
    for key_data in keys_data:
        # Check if key already exists
        existing_key = await Key.find_one(Key.value == key_data["value"])
        if existing_key:
            print(f"  Key {key_data['name']} already exists, skipping...")
            created_keys.append(existing_key)
        else:
            key = Key(**key_data)
            await key.insert()
            created_keys.append(key)
            print(f"  Created key: {key_data['name']}")

    return created_keys


async def seed_tokens(users):
    """Seed OAuth tokens for users."""
    print("Seeding tokens...")

    tokens_data = [
        {
            "userId": users[2].id,  # Jane Smith (GitHub user)
            "email": users[2].email,
            "type": "oauth",
            "identifier": "mcp:github:client",
            "token": "gho_abcdefghijklmnopqrstuvwxyz1234567890",
            "createdAt": datetime.now(UTC),
            "expiresAt": datetime.now(UTC) + timedelta(hours=24),
            "metadata": {
                "provider": "github",
                "scope": "read:user,repo",
                "token_type": "bearer",
            },
        },
        {
            "userId": users[3].id,  # OAuth User (Google)
            "email": users[3].email,
            "type": "oauth",
            "identifier": "mcp:google:client",
            "token": "ya29.a0abcdefghijklmnopqrstuvwxyz123456789",
            "createdAt": datetime.now(UTC),
            "expiresAt": datetime.now(UTC) + timedelta(hours=1),
            "metadata": {
                "provider": "google",
                "scope": "openid email profile",
                "token_type": "bearer",
                "refresh_token": "1//abcdefghijklmnopqrstuvwxyz",
            },
        },
        {
            "userId": users[1].id,  # John Developer
            "email": users[1].email,
            "type": "refresh",
            "identifier": "mcp:local:refresh",
            "token": "rt_dev_xyz123456789abcdefghijklmnopqrstuv",
            "createdAt": datetime.now(UTC),
            "expiresAt": datetime.now(UTC) + timedelta(days=30),
            "metadata": {
                "ip": "192.168.1.100",
                "user_agent": "Mozilla/5.0",
            },
        },
    ]

    created_tokens = []
    for token_data in tokens_data:
        # Check if token already exists
        existing_token = await Token.find_one(Token.token == token_data["token"])
        if existing_token:
            print(f"  Token for {token_data['identifier']} already exists, skipping...")
            created_tokens.append(existing_token)
        else:
            token = Token(**token_data)
            await token.insert()
            created_tokens.append(token)
            print(f"  Created token: {token_data['type']} for {token_data['identifier']}")

    return created_tokens


async def seed_mcp_servers(users):
    """Seed MCP servers with different authentication methods."""
    print("Seeding MCP servers...")

    # Sample server configurations with different authentication methods
    servers_data = [
        {
            "serverName": "github-copilot",
            "author": users[0].id,  # Admin user
            "path": "/github-copilot",
            "status": "active",
            "tags": ["github", "oauth", "code", "vcs"],
            "numTools": 4,
            "numStars": 0,
            "config": {
                "title": "GitHub Integration",
                "description": "GitHub repository management and code search",
                "type": "streamable-http",
                "url": "http://localhost:3001",
                "requiresOAuth": True,
                "oauth": {
                    "authorization_url": "https://github.com/login/oauth/authorize",
                    "token_url": "https://github.com/login/oauth/access_token",
                    "client_id": "7x23l1dGc5dy1s3Y-2sI",
                    "client_secret": "hc3i0fc68e0a9eece6ad4110574f797b894cba",
                    "scope": "repo read:user read:org",
                },
                "capabilities": '{"tools":{"listChanged":true},"resources":{"subscribe":false,"listChanged":true},"prompts":{"listChanged":true}}',
                "tools": "create_issue, list_repos, search_code, get_pull_requests",
                "toolFunctions": {
                    "create_issue_mcp_github": {
                        "type": "function",
                        "function": {
                            "name": "create_issue_mcp_github",
                            "description": "Create a new issue in a GitHub repository",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "repo": {"type": "string", "description": "Repository name"},
                                    "title": {"type": "string", "description": "Issue title"},
                                    "body": {"type": "string", "description": "Issue description"},
                                },
                                "required": ["repo", "title"],
                            },
                        },
                    }
                },
                "initDuration": 120,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "serverName": "tavilysearchv1",
            "author": users[1].id,  # John Developer
            "path": "/tavilysearch",
            "status": "active",
            "tags": ["search", "api-key", "tavily"],
            "numTools": 4,
            "numStars": 0,
            "config": {
                "title": "Tavily Search V1",
                "description": "Tavily search engine integration",
                "type": "streamable-http",
                "url": "https://mcp.tavily.com/mcp/",
                "requiresOAuth": False,
                "apiKey": {
                    "source": "admin",
                    "authorization_type": "custom",
                    "custom_header": "tavilyApiKey",
                    "key": "ea2ea0ba31151267149220601bd4299dfe09eccf2c4d51f3026b396fe5881a77610b",
                },
                "capabilities": '{"experimental":{},"prompts":{"listChanged":true},"resources":{"subscribe":true,"listChanged":true},"tools":{"listChanged":true}}',
                "tools": "tavily_search, tavily_extract, tavily_crawl, tavily_map",
                "toolFunctions": {
                    "tavily_search_mcp_tavilysearchv1": {
                        "type": "function",
                        "function": {
                            "name": "tavily_search_mcp_tavilysearchv1",
                            "description": "Search the web using Tavily",
                            "parameters": {
                                "type": "object",
                                "properties": {"query": {"type": "string", "description": "Search query"}},
                                "required": ["query"],
                            },
                        },
                    }
                },
                "initDuration": 170,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "serverName": "slack-notifications",
            "author": users[1].id,  # John Developer
            "path": "/slack",
            "status": "active",
            "tags": ["slack", "oauth", "notifications", "collaboration"],
            "numTools": 3,
            "numStars": 0,
            "config": {
                "title": "Slack Notifications",
                "description": "Send notifications and messages to Slack channels",
                "type": "streamable-http",
                "url": "http://slack-server:8012",
                "requiresOAuth": True,
                "oauth": {
                    "authorization_url": "https://slack.com/oauth/v2/authorize",
                    "token_url": "https://slack.com/api/oauth.v2.access",
                    "client_id": "slack_client_123",
                    "client_secret": "slack_secret_xyz789",
                    "scope": "chat:write channels:read users:read",
                },
                "capabilities": '{"tools":{"listChanged":true},"resources":{"subscribe":false,"listChanged":true}}',
                "tools": "send_message, list_channels, get_user_info",
                "toolFunctions": {
                    "send_message_mcp_slack": {
                        "type": "function",
                        "function": {
                            "name": "send_message_mcp_slack",
                            "description": "Send a message to a Slack channel",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "channel": {"type": "string", "description": "Channel ID or name"},
                                    "text": {"type": "string", "description": "Message text"},
                                },
                                "required": ["channel", "text"],
                            },
                        },
                    }
                },
                "initDuration": 95,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "serverName": "weather-service",
            "author": users[2].id,  # Jane Smith
            "path": "/weather",
            "status": "active",
            "tags": ["weather", "api-key", "data"],
            "numTools": 3,
            "numStars": 0,
            "config": {
                "title": "Weather Service",
                "description": "Real-time weather data and forecasts",
                "type": "streamable-http",
                "url": "http://weather-server:8010",
                "requiresOAuth": False,
                "apiKey": {
                    "source": "user",
                    "authorization_type": "custom",
                    "custom_header": "X-Weather-API-Key",
                    "key": "weather_api_key_abc123xyz456789",
                },
                "capabilities": '{"tools":{"listChanged":true}}',
                "tools": "get_weather, get_forecast, get_alerts",
                "toolFunctions": {
                    "get_weather_mcp_weather": {
                        "type": "function",
                        "function": {
                            "name": "get_weather_mcp_weather",
                            "description": "Get current weather for a location",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "location": {"type": "string", "description": "City name or coordinates"}
                                },
                                "required": ["location"],
                            },
                        },
                    }
                },
                "initDuration": 80,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "serverName": "public-api-service",
            "author": users[1].id,  # John Developer
            "path": "/public-api",
            "status": "active",
            "tags": ["public", "no-auth", "api", "open"],
            "numTools": 3,
            "numStars": 0,
            "config": {
                "title": "Public API Service",
                "description": "Public API service with no authentication required",
                "type": "streamable-http",
                "url": "http://public-api-server:8015",
                "requiresOAuth": False,
                "authentication": {"type": "auto"},
                "capabilities": '{"tools":{"listChanged":true}}',
                "tools": "get_public_data, search_content, get_stats",
                "toolFunctions": {
                    "get_public_data_mcp_public": {
                        "type": "function",
                        "function": {
                            "name": "get_public_data_mcp_public",
                            "description": "Get public data from the API",
                            "parameters": {
                                "type": "object",
                                "properties": {"endpoint": {"type": "string", "description": "API endpoint path"}},
                                "required": ["endpoint"],
                            },
                        },
                    }
                },
                "initDuration": 50,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "serverName": "google-workspace",
            "author": users[1].id,  # John Developer
            "path": "/google-workspace",
            "status": "active",
            "tags": ["google", "oauth", "productivity", "cloud"],
            "numTools": 4,
            "numStars": 0,
            "config": {
                "title": "Google Workspace",
                "description": "Google Drive, Docs, Sheets, and Calendar integration",
                "type": "streamable-http",
                "url": "http://google-workspace-server:8014",
                "requiresOAuth": True,
                "oauth": {
                    "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_url": "https://oauth2.googleapis.com/token",
                    "client_id": "google_client_12345",
                    "client_secret": "google_secret_abc789xyz",
                    "scope": "https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/calendar",
                },
                "capabilities": '{"tools":{"listChanged":true},"resources":{"subscribe":false,"listChanged":true}}',
                "tools": "list_files, create_document, read_sheet, schedule_event",
                "toolFunctions": {
                    "list_files_mcp_google": {
                        "type": "function",
                        "function": {
                            "name": "list_files_mcp_google",
                            "description": "List files in Google Drive",
                            "parameters": {
                                "type": "object",
                                "properties": {"folder": {"type": "string", "description": "Folder ID (optional)"}},
                            },
                        },
                    }
                },
                "initDuration": 150,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "serverName": "atlassian-jira",
            "author": users[2].id,  # Jane Smith
            "path": "/jira",
            "status": "active",
            "tags": ["atlassian", "jira", "oauth", "project-management"],
            "numTools": 4,
            "numStars": 0,
            "config": {
                "title": "Atlassian JIRA",
                "description": "JIRA issue tracking and project management",
                "type": "streamable-http",
                "url": "http://atlassian-server:8005",
                "requiresOAuth": True,
                "oauth": {
                    "authorization_url": "https://auth.atlassian.com/authorize",
                    "token_url": "https://auth.atlassian.com/oauth/token",
                    "client_id": "jira_client_456",
                    "client_secret": "jira_secret_def456uvw",
                    "scope": "read:jira-work write:jira-work read:jira-user",
                },
                "capabilities": '{"tools":{"listChanged":true},"resources":{"subscribe":false,"listChanged":true}}',
                "tools": "create_issue, update_issue, search_issues, get_project",
                "toolFunctions": {
                    "create_issue_mcp_jira": {
                        "type": "function",
                        "function": {
                            "name": "create_issue_mcp_jira",
                            "description": "Create a new JIRA issue",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "project": {"type": "string", "description": "Project key"},
                                    "summary": {"type": "string", "description": "Issue summary"},
                                    "description": {"type": "string", "description": "Issue description"},
                                },
                                "required": ["project", "summary"],
                            },
                        },
                    }
                },
                "initDuration": 110,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
        {
            "serverName": "currenttime-server",
            "author": users[0].id,  # Admin user
            "path": "/time",
            "status": "active",
            "tags": ["time", "utility", "no-auth"],
            "numTools": 3,
            "numStars": 0,
            "config": {
                "title": "Current Time Server",
                "description": "Get current time in various formats and timezones",
                "type": "streamable-http",
                "url": "http://currenttime-server:8000",
                "requiresOAuth": False,
                "authentication": {"type": "auto"},
                "capabilities": '{"tools":{"listChanged":true}}',
                "tools": "get_time, get_timezone, convert_time",
                "toolFunctions": {
                    "get_time_mcp_currenttime": {
                        "type": "function",
                        "function": {
                            "name": "get_time_mcp_currenttime",
                            "description": "Get current time in specified format",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "format": {"type": "string", "description": "Time format (ISO, unix, etc.)"},
                                    "timezone": {"type": "string", "description": "Timezone (optional)"},
                                },
                            },
                        },
                    }
                },
                "initDuration": 30,
            },
            "createdAt": datetime.now(UTC),
            "updatedAt": datetime.now(UTC),
        },
    ]

    created_servers = []
    for server_data in servers_data:
        # Check if server already exists
        existing_server = await ExtendedMCPServer.find_one(ExtendedMCPServer.serverName == server_data["serverName"])
        if existing_server:
            print(f"  Server {server_data['serverName']} already exists, skipping...")
            created_servers.append(existing_server)
        else:
            # Encrypt sensitive authentication fields before storing
            server_data["config"] = encrypt_auth_fields(server_data["config"])

            server = ExtendedMCPServer(**server_data)
            await server.insert()
            created_servers.append(server)

            # Determine auth type for logging
            auth_type = "none"
            if server_data["config"].get("requiresOAuth"):
                auth_type = "oauth"
            elif "apiKey" in server_data["config"]:
                auth_type = "apiKey"
            elif "authentication" in server_data["config"]:
                auth_type = server_data["config"]["authentication"].get("type", "none")

            print(
                f"  Created server: {server_data['serverName']} (auth: {auth_type}, tools: {server_data['numTools']})"
            )

    return created_servers


async def seed_acl_entries(users, servers):
    print("Seeding ACL Entries...")
    # Grant admin OWNER on all servers, authors OWNER, others VIEWER
    acl_entries = []
    admin_user = next((u for u in users if getattr(u, "role", "").upper() == "ADMIN"), None)
    for server in servers:
        # Create a public entry
        print(f"Seeding public ACL Entry for server: {server.id}")
        existing_public_acl = await ExtendedAclEntry.find_one(
            {
                "principalType": PrincipalType.PUBLIC,
                "principalId": None,
                "resourceType": ResourceType.MCPSERVER,
                "resourceId": server.id,
            }
        )
        if existing_public_acl:
            print(f"  Public ACL entry for server {server.serverName} already exists, skipping...")
            acl_entries.append(existing_public_acl)
        else:
            public_acl_entry = ExtendedAclEntry(
                principalType=PrincipalType.PUBLIC,
                principalId=None,
                resourceType=ResourceType.MCPSERVER.value,
                resourceId=server.id,
                permBits=PermissionBits.VIEW,
                grantedAt=datetime.now(UTC),
                createdAt=datetime.now(UTC),
                updatedAt=datetime.now(UTC),
            )
            created_public_entry = await public_acl_entry.insert()
            acl_entries.append(created_public_entry)
            print(f"  Created public ACL entry server {server.serverName}")

        for user in users:
            print(f"Seeding ACL Entry for user: {user.id} on server: {server.serverName}")
            # Admin and Authors get OWNER on all servers
            if user.id == admin_user.id or user.id == server.author:
                perm_bits = RoleBits.OWNER

                existing_acl = await ExtendedAclEntry.find_one(
                    {
                        "principalType": PrincipalType.USER,
                        "principalId": user.id,
                        "resourceType": ResourceType.MCPSERVER,
                        "resourceId": server.id,
                    }
                )
                if existing_acl:
                    print(f"  ACL entry for user {user} and server {server.serverName} already exists, skipping...")
                    acl_entries.append(existing_acl)
                else:
                    acl_entry = ExtendedAclEntry(
                        principalType=PrincipalType.USER,
                        principalId=user.id,
                        resourceType=ResourceType.MCPSERVER.value,
                        resourceId=server.id,
                        permBits=perm_bits,
                        grantedAt=datetime.now(UTC),
                        createdAt=datetime.now(UTC),
                        updatedAt=datetime.now(UTC),
                    )
                    await acl_entry.insert()
                    acl_entries.append(acl_entry)
                    print(
                        f"  Created ACL entry for user {user.username} and server {server.serverName} (permBits={perm_bits})"
                    )

    print(f"  - {len(acl_entries)} ACL entries seeded")
    return acl_entries


async def clean_database():
    """Clean all seeded collections."""
    print("Cleaning database collections...")

    try:
        # Delete all documents from each collection
        user_count = await User.delete_all()
        print(f"  Deleted {user_count.deleted_count} users")

        key_count = await Key.delete_all()
        print(f"  Deleted {key_count.deleted_count} keys")

        token_count = await Token.delete_all()
        print(f"  Deleted {token_count.deleted_count} tokens")

        server_count = await ExtendedMCPServer.delete_all()
        print(f"  Deleted {server_count.deleted_count} MCP servers")

        acl_count = await ExtendedAclEntry.delete_all()
        print(f"  Deleted {acl_count.deleted_count} ACL Entries")

        group_count = await Group.delete_all()
        print(f"  Deleted {group_count.deleted_count} groups")

        print("\n" + "=" * 60)
        print("✅ Database cleaned successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"❌ Error cleaning database: {e}")
        raise


async def main():
    """Main function to seed or clean data."""
    # Parse command line arguments
    command = sys.argv[1] if len(sys.argv) > 1 else "seed"

    if command not in ["seed", "clean"]:
        print(f"❌ Unknown command: {command}")
        print("\nUsage:")
        print("  python scripts/seed_mongodb.py          # Seed data (default)")
        print("  python scripts/seed_mongodb.py seed     # Seed data")
        print("  python scripts/seed_mongodb.py clean    # Clean all collections")
        sys.exit(1)

    # Get MongoDB connection details from environment
    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis")

    # Parse database name from URI if present
    db_name = None
    if "/" in mongo_uri.split("://")[-1]:
        # Extract database name from URI (everything after the last /)
        uri_path = mongo_uri.split("://")[-1]
        if "/" in uri_path:
            db_name = uri_path.split("/")[-1].split("?")[0]  # Handle query params

    # Fall back to default if not in URI
    if not db_name:
        db_name = "jarvis"

    print(f"Connecting to MongoDB at {mongo_uri}...")
    print(f"Database: {db_name}")
    print(f"Command: {command}\n")

    try:
        # Connect to MongoDB
        await MongoDB.connect_db(db_name=db_name)
        print("Connected to MongoDB successfully!\n")

        if command == "clean":
            # Clean database
            await clean_database()
        else:
            # Seed data in order
            groups = await seed_groups()
            print()

            users = await seed_users()
            print()

            keys = await seed_keys(users)
            print()

            tokens = await seed_tokens(users)
            print()

            servers = await seed_mcp_servers(users)
            print()

            aclEntries = await seed_acl_entries(users, servers)
            print()

            print("=" * 60)
            print("✅ Database seeding completed successfully!")
            print("=" * 60)
            print("Created/Found:")
            print(f"  - {len(groups)} groups" if groups else "  - 0 groups (model not available)")
            print(f"  - {len(users)} users")
            print(f"  - {len(keys)} API keys")
            print(f"  - {len(tokens)} tokens")
            print(f"  - {len(servers)} MCP servers")
            print(f"  - {len(aclEntries)} ACL entries")
            print(
                f"    • API Key auth: {sum(1 for s in servers if s.config.get('authentication', {}).get('type') == 'api_key')}"
            )
            print(
                f"    • OAuth auth: {sum(1 for s in servers if s.config.get('authentication', {}).get('type') == 'oauth')}"
            )
            print(
                f"    • No auth: {sum(1 for s in servers if s.config.get('authentication', {}).get('type') == 'none')}"
            )

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        # Close connection
        await MongoDB.close_db()
        print("\nMongoDB connection closed.")


def cli():
    """Entry point for command-line script."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
