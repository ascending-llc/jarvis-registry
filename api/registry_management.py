#!/usr/bin/env python3
"""
MCP Gateway Registry Management CLI.

High-level wrapper for the RegistryClient providing command-line interface
for server registration, management, group operations, and A2A agent management.

Server Management:
    # Register a server from JSON config
    uv run python registry_management.py register --config /path/to/config.json

    # List all servers
    uv run python registry_management.py list

    # Toggle server status
    uv run python registry_management.py toggle --path /cloudflare-docs

    # Remove server
    uv run python registry_management.py remove --path /cloudflare-docs

    # Health check
    uv run python registry_management.py healthcheck

    # Rate a server (1-5 stars)
    uv run python registry_management.py server-rate --path /cloudflare-docs --rating 5

    # Get server rating information
    uv run python registry_management.py server-rating --path /cloudflare-docs

    # Get security scan results for a server
    uv run python registry_management.py security-scan --path /cloudflare-docs

    # Trigger manual security scan (admin only)
    uv run python registry_management.py rescan --path /cloudflare-docs

Group Management:
    # Add server to groups
    uv run python registry_management.py add-to-groups --server my-server --groups group1,group2

    # List all groups
    uv run python registry_management.py list-groups

Agent Management (A2A):
    # Register an agent
    uv run python registry_management.py agent-register --config /path/to/agent.json

    # List all agents
    uv run python registry_management.py agent-list

    # Get agent details
    uv run python registry_management.py agent-get --path /code-reviewer

    # Toggle agent status
    uv run python registry_management.py agent-toggle --path /code-reviewer --enabled true

    # Delete agent
    uv run python registry_management.py agent-delete --path /code-reviewer

    # Rate an agent (1-5 stars)
    uv run python registry_management.py agent-rate --path /code-reviewer --rating 5

    # Get agent rating information
    uv run python registry_management.py agent-rating --path /code-reviewer

    # Discover agents by skills
    uv run python registry_management.py agent-discover --skills code_analysis,bug_detection

    # Semantic agent search
    uv run python registry_management.py agent-search --query "agents that analyze code"

Anthropic Registry API (v0.1):
    # List all servers
    uv run python registry_management.py anthropic-list

    # List all servers with raw JSON output
    uv run python registry_management.py anthropic-list --raw

    # List versions for a specific server
    uv run python registry_management.py anthropic-versions --server-name "io.mcpgateway/example-server"

    # Get server details
    uv run python registry_management.py anthropic-get --server-name "io.mcpgateway/example-server" --version latest

User Management (IAM):
    # List all Keycloak users
    uv run python registry_management.py user-list

    # Search for specific users
    uv run python registry_management.py user-list --search admin

    # Create M2M service account
    uv run python registry_management.py user-create-m2m --name my-service --groups registry-admin

    # Create human user
    uv run python registry_management.py user-create-human --username john.doe --email john@example.com --first-name John --last-name Doe --groups registry-admin

    # Delete user
    uv run python registry_management.py user-delete --username john.doe

Group Management (IAM):
    # List IAM groups
    uv run python registry_management.py group-list

    # Create a new IAM group
    uv run python registry_management.py group-create --name developers --description "Developer team group"

    # Delete an IAM group
    uv run python registry_management.py group-delete --name developers --force

Global Options (can be set via environment variables or command-line arguments):
    --registry-url URL       Registry base URL (overrides REGISTRY_URL env var)
    --aws-region REGION      AWS region (overrides AWS_REGION env var)
    --keycloak-url URL       Keycloak base URL (overrides KEYCLOAK_URL env var)
    --token-file PATH        Path to file containing JWT token (bypasses token script)

Environment Variables (used if command-line options not provided):
    REGISTRY_URL: Registry base URL (e.g., https://registry.mycorp.click)
    AWS_REGION: AWS region where Keycloak and SSM are deployed (e.g., us-east-1)
    KEYCLOAK_URL: Keycloak base URL (e.g., https://kc.us-east-1.mycorp.click)

Environment Variables (Optional):
    CLIENT_NAME: Keycloak client name (default: registry-admin-bot)
    GET_TOKEN_SCRIPT: Path to get-m2m-token.sh script

Local Development (running against local Docker Compose setup):
    When running the solution locally with Docker Compose, you can use the --token-file
    option to provide a pre-generated JWT token instead of dynamically fetching one.

    Step 1: Generate credentials using the credentials provider script:
        cd credentials-provider
        ./generate_creds.sh

    Step 2: Use the generated token file with the CLI:
        uv run python api/registry_management.py --debug \\
            --registry-url http://localhost \\
            --token-file .oauth-tokens/ingress.json \\
            list 2>&1 | tee debug.log

    The credentials-provider/generate_creds.sh script creates tokens in .oauth-tokens/
    directory. The ingress.json token file contains the admin JWT token that can be
    used with the registry management CLI.

    Other examples for local development:
        # List users
        uv run python api/registry_management.py --debug \\
            --registry-url http://localhost \\
            --token-file .oauth-tokens/ingress.json \\
            user-list

        # Health check
        uv run python api/registry_management.py --debug \\
            --registry-url http://localhost \\
            --token-file .oauth-tokens/ingress.json \\
            healthcheck

        # Create M2M account
        uv run python api/registry_management.py --debug \\
            --registry-url http://localhost \\
            --token-file .oauth-tokens/ingress.json \\
            user-create-m2m --name test-bot --groups developers
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from registry_client import (
    AgentProvider,
    AgentRegistration,
    AgentRescanResponse,
    AgentSecurityScanResponse,
    AgentVisibility,
    AnthropicServerList,
    AnthropicServerResponse,
    InternalServiceRegistration,
    RatingInfoResponse,
    RatingResponse,
    RegistryClient,
    RescanResponse,
    SecurityScanResult,
    Skill,
)

from registry.core.config import settings

# Configure logging
logging.basicConfig(level=settings.log_level, format=settings.log_format)

logger = logging.getLogger(__name__)


def _get_registry_url(cli_value: str | None = None) -> str:
    """
    Get registry URL from command-line argument or environment variable.

    Args:
        cli_value: Command-line argument value (overrides environment variable)

    Returns:
        Registry base URL

    Raises:
        ValueError: If REGISTRY_URL is not provided
    """
    registry_url = cli_value or os.getenv("REGISTRY_URL")
    if not registry_url:
        raise ValueError(
            "REGISTRY_URL is required.\n"
            "Set via environment variable or --registry-url option:\n"
            "  export REGISTRY_URL=https://registry.mycorp.click\n"
            "  OR\n"
            "  --registry-url https://registry.mycorp.click"
        )

    logger.debug(f"Using registry URL: {registry_url}")
    return registry_url


def _get_client_name() -> str:
    """
    Get Keycloak client name from environment variable or default.

    Returns:
        Client name
    """
    client_name = os.getenv("CLIENT_NAME", "registry-admin-bot")
    logger.debug(f"Using client name: {client_name}")
    return client_name


def _get_token_script() -> str:
    """
    Get path to get-m2m-token.sh script.

    Returns:
        Script path
    """
    # Default to get-m2m-token.sh in the same directory as this script
    script_dir = Path(__file__).parent
    default_script = str(script_dir / "get-m2m-token.sh")
    script_path = os.getenv("GET_TOKEN_SCRIPT", default_script)
    logger.debug(f"Using token script: {script_path}")
    return script_path


def _get_jwt_token(aws_region: str | None = None, keycloak_url: str | None = None) -> str:
    """
    Retrieve JWT token using get-m2m-token.sh script.

    Args:
        aws_region: AWS region (passed to script via --aws-region)
        keycloak_url: Keycloak URL (passed to script via --keycloak-url)

    Returns:
        JWT access token

    Raises:
        RuntimeError: If token retrieval fails
    """
    client_name = _get_client_name()
    script_path = _get_token_script()

    try:
        # Redact client name in logs for security
        logger.debug(f"Retrieving token for client: {client_name}")

        # Build command with optional arguments
        cmd = [script_path]
        if aws_region:
            cmd.extend(["--aws-region", aws_region])
        if keycloak_url:
            cmd.extend(["--keycloak-url", keycloak_url])
        cmd.append(client_name)

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        token = result.stdout.strip()

        if not token:
            raise RuntimeError("Empty token returned from get-m2m-token.sh")

        # Redact token in logs - show only first 8 characters
        redacted_token = f"{token[:8]}..." if len(token) > 8 else "***"
        logger.debug(f"Successfully retrieved JWT token: {redacted_token}")
        return token

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to retrieve token: {e.stderr}")
        raise RuntimeError(f"Token retrieval failed: {e.stderr}") from e
    except Exception as e:
        logger.error(f"Unexpected error retrieving token: {e}")
        raise RuntimeError(f"Token retrieval error: {e}") from e


def _load_json_config(config_path: str) -> dict[str, Any]:
    """
    Load JSON configuration file.

    Args:
        config_path: Path to JSON config file

    Returns:
        Configuration dictionary

    Raises:
        FileNotFoundError: If config file not found
        json.JSONDecodeError: If config file is invalid JSON
    """
    config_file = Path(config_path)

    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file) as f:
        config = json.load(f)

    logger.debug(f"Loaded configuration from {config_path}")
    return config


def _create_client(args: argparse.Namespace) -> RegistryClient:
    """
    Create and return a configured RegistryClient instance.

    Args:
        args: Command arguments containing optional CLI values

    Returns:
        RegistryClient instance

    Raises:
        RuntimeError: If token retrieval fails
        FileNotFoundError: If token file not found
        ValueError: If required configuration is missing
    """
    # Check all required configuration upfront
    missing_params = []

    # Check REGISTRY_URL
    registry_url = args.registry_url or os.getenv("REGISTRY_URL")
    if not registry_url:
        missing_params.append("REGISTRY_URL")

    # Check if token file is provided
    if hasattr(args, "token_file") and args.token_file:
        token_path = Path(args.token_file)
        if not token_path.exists():
            raise FileNotFoundError(f"Token file not found: {args.token_file}")

        logger.debug(f"Loading token from file: {args.token_file}")

        # Try to parse as JSON first (token files from generate-agent-token.sh)
        try:
            with open(token_path) as f:
                token_data = json.load(f)
            # Extract access_token from JSON structure
            token = token_data.get("access_token")
            if not token:
                raise RuntimeError(f"No 'access_token' field found in token file: {args.token_file}")
        except json.JSONDecodeError:
            # Fall back to plain text token file
            token = token_path.read_text().strip()

        if not token:
            raise RuntimeError(f"Empty token in file: {args.token_file}")

        # Redact token in logs - show only first 8 characters
        redacted_token = f"{token[:8]}..." if len(token) > 8 else "***"
        logger.debug(f"Successfully loaded token from file: {redacted_token}")
    else:
        # Check parameters needed for token script
        aws_region = args.aws_region or os.getenv("AWS_REGION")
        keycloak_url = args.keycloak_url or os.getenv("KEYCLOAK_URL")

        if not aws_region:
            missing_params.append("AWS_REGION")
        if not keycloak_url:
            missing_params.append("KEYCLOAK_URL")

        # If any parameters are missing, raise comprehensive error
        if missing_params:
            error_msg = "Missing required configuration:\n\n"
            for param in missing_params:
                error_msg += f"  - {param}\n"
            error_msg += "\nSet via environment variables or command-line options:\n\n"
            if "REGISTRY_URL" in missing_params:
                error_msg += "  export REGISTRY_URL=https://registry.example.com\n"
                error_msg += "  OR use --registry-url https://registry.example.com\n\n"
            if "AWS_REGION" in missing_params:
                error_msg += "  export AWS_REGION=us-east-1\n"
                error_msg += "  OR use --aws-region us-east-1\n\n"
            if "KEYCLOAK_URL" in missing_params:
                error_msg += "  export KEYCLOAK_URL=https://keycloak.example.com\n"
                error_msg += "  OR use --keycloak-url https://keycloak.example.com\n\n"
            error_msg += "Alternatively, use --token-file to provide a pre-generated JWT token."
            raise ValueError(error_msg)

        token = _get_jwt_token(aws_region=aws_region, keycloak_url=keycloak_url)

    # Final check for registry URL (in case token file path was provided)
    if missing_params and "REGISTRY_URL" in missing_params:
        raise ValueError(
            "REGISTRY_URL is required.\n"
            "Set via environment variable or --registry-url option:\n"
            "  export REGISTRY_URL=https://registry.example.com\n"
            "  OR\n"
            "  --registry-url https://registry.example.com"
        )

    return RegistryClient(registry_url=registry_url, token=token)


def cmd_register(args: argparse.Namespace) -> int:
    """
    Register a new server from JSON configuration.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        config = _load_json_config(args.config)

        # Convert config to InternalServiceRegistration
        # Handle both old and new config formats
        registration = InternalServiceRegistration(
            service_path=config.get("path") or config.get("service_path"),
            name=config.get("server_name") or config.get("name"),
            description=config.get("description"),
            proxy_pass_url=config.get("proxy_pass_url"),
            auth_provider=config.get("auth_provider"),
            auth_type=config.get("auth_type"),
            supported_transports=config.get("supported_transports"),
            headers=config.get("headers"),
            tool_list_json=config.get("tool_list_json"),
            overwrite=args.overwrite,
        )

        client = _create_client(args)
        response = client.register_service(registration)

        logger.info(f"Server registered successfully: {response.path}")
        logger.info(f"Message: {response.message}")
        return 0

    except FileNotFoundError as e:
        logger.error(f"Configuration file error: {e}")
        return 1
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON configuration: {e}")
        return 1
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """
    List all registered servers.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.list_services()

        if not response.servers:
            logger.info("No servers registered")
            return 0

        # Print raw JSON if requested
        if hasattr(args, "json") and args.json:
            import json

            print(json.dumps(response.model_dump(), indent=2, default=str))
            return 0

        logger.info(f"Found {len(response.servers)} registered servers:\n")

        for server in response.servers:
            status_icon = "✓" if server.is_enabled else "✗"
            health_icon = {"healthy": "🟢", "unhealthy": "🔴", "unknown": "⚪", "disabled": "⚫"}.get(
                server.health_status.value, "⚪"
            )

            print(f"{status_icon} {health_icon} {server.path}")
            print(f"   Name: {server.display_name}")
            print(f"   Description: {server.description}")
            print(f"   Enabled: {server.is_enabled}")
            print(f"   Health: {server.health_status.value}")
            print()

        return 0

    except Exception as e:
        logger.error(f"List operation failed: {e}")
        return 1


def cmd_toggle(args: argparse.Namespace) -> int:
    """
    Toggle server enabled/disabled status.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.toggle_service(args.path)

        status = "enabled" if response.is_enabled else "disabled"
        logger.info(f"Server {response.path} is now {status}")
        logger.info(f"Message: {response.message}")
        return 0

    except Exception as e:
        logger.error(f"Toggle operation failed: {e}")
        return 1


def cmd_remove(args: argparse.Namespace) -> int:
    """
    Remove a server from the registry.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        if not args.force:
            confirmation = input(f"Remove server {args.path}? (yes/no): ")
            if confirmation.lower() != "yes":
                logger.info("Operation cancelled")
                return 0

        client = _create_client(args)
        client.remove_service(args.path)

        logger.info(f"Server removed successfully: {args.path}")
        return 0

    except Exception as e:
        logger.error(f"Remove operation failed: {e}")
        return 1


def cmd_healthcheck(args: argparse.Namespace) -> int:
    """
    Perform health check on all servers.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.healthcheck()

        logger.info(f"Health check status: {response.get('status', 'unknown')}")
        logger.info("\nHealth check results:")
        print(json.dumps(response, indent=2))
        return 0

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return 1


def cmd_add_to_groups(args: argparse.Namespace) -> int:
    """
    Add server to user groups.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        groups = [g.strip() for g in args.groups.split(",")]
        client = _create_client(args)
        client.add_server_to_groups(args.server, groups)

        logger.info(f"Server {args.server} added to groups: {', '.join(groups)}")
        return 0

    except Exception as e:
        logger.error(f"Add to groups failed: {e}")
        return 1


def cmd_remove_from_groups(args: argparse.Namespace) -> int:
    """
    Remove server from user groups.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        groups = [g.strip() for g in args.groups.split(",")]
        client = _create_client(args)
        client.remove_server_from_groups(args.server, groups)

        logger.info(f"Server {args.server} removed from groups: {', '.join(groups)}")
        return 0

    except Exception as e:
        logger.error(f"Remove from groups failed: {e}")
        return 1


def cmd_create_group(args: argparse.Namespace) -> int:
    """
    Create a new user group.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        client.create_group(group_name=args.name, description=args.description, create_in_keycloak=args.keycloak)

        logger.info(f"Group created successfully: {args.name}")
        return 0

    except Exception as e:
        logger.error(f"Create group failed: {e}")
        return 1


def cmd_delete_group(args: argparse.Namespace) -> int:
    """
    Delete a user group.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        if not args.force:
            confirmation = input(f"Delete group {args.name}? (yes/no): ")
            if confirmation.lower() != "yes":
                logger.info("Operation cancelled")
                return 0

        client = _create_client(args)
        client.delete_group(group_name=args.name, delete_from_keycloak=args.keycloak, force=args.force)

        logger.info(f"Group deleted successfully: {args.name}")
        return 0

    except Exception as e:
        logger.error(f"Delete group failed: {e}")
        return 1


def cmd_list_groups(args: argparse.Namespace) -> int:
    """
    List all user groups.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.list_groups(include_keycloak=not args.no_keycloak, include_scopes=not args.no_scopes)

        if not response.groups:
            logger.info("No groups found")
            return 0

        logger.info(f"Found {len(response.groups)} groups:\n")

        for group in response.groups:
            print(f"Group: {group.get('name', 'Unknown')}")
            if "description" in group:
                print(f"  Description: {group['description']}")
            if "servers" in group:
                print(f"  Servers: {', '.join(group['servers']) if group['servers'] else 'None'}")
            print()

        return 0

    except Exception as e:
        logger.error(f"List groups failed: {e}")
        return 1


def cmd_server_rate(args: argparse.Namespace) -> int:
    """
    Rate a server (1-5 stars).

    Args:
        args: Command arguments with path and rating

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: RatingResponse = client.rate_server(path=args.path, rating=args.rating)

        logger.info(f"✓ {response.message}")
        logger.info(f"Average rating: {response.average_rating:.2f} stars")

        return 0

    except Exception as e:
        logger.error(f"Failed to rate server: {e}")
        return 1


def cmd_server_rating(args: argparse.Namespace) -> int:
    """
    Get rating information for a server.

    Args:
        args: Command arguments with path

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: RatingInfoResponse = client.get_server_rating(path=args.path)

        logger.info(f"\nRating for server '{args.path}':")
        logger.info(f"  Average: {response.num_stars:.2f} stars")
        logger.info(f"  Total ratings: {len(response.rating_details)}")

        if response.rating_details:
            logger.info("\nIndividual ratings (most recent):")
            # Show first 10 ratings
            for detail in response.rating_details[:10]:
                logger.info(f"  {detail.user}: {detail.rating} stars")

            if len(response.rating_details) > 10:
                logger.info(f"  ... and {len(response.rating_details) - 10} more")

        return 0

    except Exception as e:
        logger.error(f"Failed to get ratings: {e}")
        return 1


def cmd_security_scan(args: argparse.Namespace) -> int:
    """
    Get security scan results for a server.

    Args:
        args: Command arguments with path and optional json flag

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: SecurityScanResult = client.get_security_scan(path=args.path)

        if args.json:
            # Output raw JSON
            print(json.dumps(response.model_dump(), indent=2, default=str))
        else:
            # Pretty print results
            logger.info(f"\nSecurity scan results for server '{args.path}':")

            # Display analysis results by analyzer
            if response.analysis_results:
                for analyzer_name, analyzer_data in response.analysis_results.items():
                    logger.info(f"\n  Analyzer: {analyzer_name}")
                    if isinstance(analyzer_data, dict) and "findings" in analyzer_data:
                        findings = analyzer_data["findings"]
                        logger.info(f"    Findings: {len(findings)}")
                        for finding in findings[:5]:  # Show first 5
                            severity = finding.get("severity", "UNKNOWN")
                            tool_name = finding.get("tool_name", "unknown")
                            logger.info(f"      - {tool_name}: {severity}")
                        if len(findings) > 5:
                            logger.info(f"      ... and {len(findings) - 5} more")

            # Display tool results summary
            if response.tool_results:
                logger.info(f"\n  Total tools scanned: {len(response.tool_results)}")
                safe_count = sum(1 for tool in response.tool_results if tool.get("is_safe", False))
                unsafe_count = len(response.tool_results) - safe_count
                logger.info(f"  Safe tools: {safe_count}")
                if unsafe_count > 0:
                    logger.info(f"  Unsafe tools: {unsafe_count}")
                    logger.warning("\n  WARNING: Some tools flagged as potentially unsafe!")

        return 0

    except Exception as e:
        logger.error(f"Failed to get security scan results: {e}")
        return 1


def cmd_rescan(args: argparse.Namespace) -> int:
    """
    Trigger manual security scan for a server (admin only).

    Args:
        args: Command arguments with path and optional json flag

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: RescanResponse = client.rescan_server(path=args.path)

        if args.json:
            # Output raw JSON
            print(json.dumps(response.model_dump(), indent=2, default=str))
        else:
            # Pretty print results
            safety_status = "SAFE" if response.is_safe else "UNSAFE"
            logger.info(f"\nSecurity scan completed for server '{args.path}':")
            logger.info(f"  Status: {safety_status}")
            logger.info(f"  Scan timestamp: {response.scan_timestamp}")
            logger.info(f"  Analyzers used: {', '.join(response.analyzers_used)}")
            logger.info("\n  Severity counts:")
            logger.info(f"    Critical: {response.critical_issues}")
            logger.info(f"    High: {response.high_severity}")
            logger.info(f"    Medium: {response.medium_severity}")
            logger.info(f"    Low: {response.low_severity}")

            if response.scan_failed:
                logger.error(f"\n  Scan failed: {response.error_message}")
                return 1

            if not response.is_safe:
                logger.warning("\n  WARNING: Server flagged as potentially unsafe!")

        return 0

    except Exception as e:
        logger.error(f"Failed to trigger security scan: {e}")
        return 1


# Agent Management Command Handlers


def cmd_agent_register(args: argparse.Namespace) -> int:
    """
    Register a new A2A agent from JSON configuration.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        config_path = Path(args.config)
        if not config_path.exists():
            logger.error(f"Config file not found: {config_path}")
            return 1

        with open(config_path) as f:
            config = json.load(f)

        # Convert skills list of dicts to Skill objects
        # Handle both 'input_schema' and 'parameters' field names
        # Also handle 'id' vs 'name' field for skill identifier
        skills = []
        for skill_data in config.get("skills", []):
            # Get skill identifier - prefer 'id', fall back to 'name'
            skill_id = skill_data.get("id") or skill_data.get("name", "")
            skill_name = skill_data.get("name", skill_id)

            # Normalize field names
            skill_dict = {
                "id": skill_id,  # Always include id field
                "name": skill_name,
                "description": skill_data.get("description", ""),
                "tags": skill_data.get("tags", []),  # Include tags field
            }
            # Use 'input_schema' if present, otherwise use 'parameters'
            if "input_schema" in skill_data:
                skill_dict["input_schema"] = skill_data["input_schema"]
            elif "parameters" in skill_data:
                skill_dict["input_schema"] = skill_data["parameters"]

            skills.append(Skill(**skill_dict))
        config["skills"] = skills

        # Provider is now a dict object per A2A spec {organization, url}
        # No conversion needed - pass it through as-is

        # Convert visibility string to enum if present
        if "visibility" in config:
            try:
                config["visibility"] = AgentVisibility(config["visibility"].lower())
            except ValueError:
                logger.warning(f"Unknown visibility '{config['visibility']}', using 'public'")
                config["visibility"] = AgentVisibility.PUBLIC

        # Handle security_schemes conversion
        # Normalize common security type variations to A2A spec values
        if "security_schemes" in config:
            transformed_schemes = {}
            for scheme_name, scheme_data in config["security_schemes"].items():
                scheme_type = scheme_data.get("type", "").lower()
                # Normalize to A2A spec values: apiKey, http, oauth2, openIdConnect
                # Keep 'http' as is (for bearer auth), not 'bearer'
                type_map = {
                    "http": "http",  # HTTP auth (including bearer)
                    "bearer": "http",  # Bearer is a type of HTTP auth
                    "apikey": "apiKey",
                    "api_key": "apiKey",
                    "oauth2": "oauth2",
                    "openidconnect": "openIdConnect",
                    "openid": "openIdConnect",
                }
                mapped_type = type_map.get(scheme_type, "http")

                # Preserve all fields from the original scheme data
                transformed_scheme = dict(scheme_data)
                transformed_scheme["type"] = mapped_type

                transformed_schemes[scheme_name] = transformed_scheme
            config["security_schemes"] = transformed_schemes

        # Remove fields that aren't in AgentRegistration model
        valid_fields = {
            "protocol_version",
            "name",
            "description",
            "path",
            "url",
            "version",
            "capabilities",
            "default_input_modes",
            "default_output_modes",
            "provider",
            "security_schemes",
            "skills",
            "tags",
            "visibility",
            "license",
        }
        config = {k: v for k, v in config.items() if k in valid_fields}

        agent = AgentRegistration(**config)
        client = _create_client(args)
        response = client.register_agent(agent)

        logger.info(f"Agent registered successfully: {response.agent.name} at {response.agent.path}")
        print(
            json.dumps(
                {
                    "message": response.message,
                    "agent": {
                        "name": response.agent.name,
                        "path": response.agent.path,
                        "url": response.agent.url,
                        "num_skills": response.agent.num_skills,
                        "is_enabled": response.agent.is_enabled,
                    },
                },
                indent=2,
            )
        )
        return 0

    except Exception as e:
        logger.error(f"Agent registration failed: {e}")
        logger.debug("Full error details:", exc_info=True)
        return 1


def cmd_agent_list(args: argparse.Namespace) -> int:
    """
    List all A2A agents.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.list_agents(
            query=args.query if hasattr(args, "query") else None,
            enabled_only=args.enabled_only if hasattr(args, "enabled_only") else False,
            visibility=args.visibility if hasattr(args, "visibility") else None,
        )

        # Debug mode: print full JSON response
        if args.debug:
            logger.debug("Full JSON response from API:")
            print(json.dumps(response.model_dump(by_alias=True), indent=2, default=str))
            print()

        if not response.agents:
            logger.info("No agents found")
            return 0

        logger.info(f"Found {len(response.agents)} agents:\n")
        for agent in response.agents:
            status = "✓" if agent.is_enabled else "✗"
            print(f"{status} {agent.name} ({agent.path})")
            print(f"  {agent.description}")
            print()

        return 0

    except Exception as e:
        logger.error(f"List agents failed: {e}")
        return 1


def cmd_agent_get(args: argparse.Namespace) -> int:
    """
    Get detailed information about a specific agent.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        agent = client.get_agent(args.path)

        logger.info(f"Retrieved agent: {agent.name}")
        print(
            json.dumps(
                {
                    "name": agent.name,
                    "path": agent.path,
                    "description": agent.description,
                    "url": agent.url,
                    "version": agent.version,
                    "provider": agent.provider.model_dump() if agent.provider else None,
                    "is_enabled": agent.is_enabled,
                    "visibility": agent.visibility,
                    "skills": [{"name": skill.name, "description": skill.description} for skill in agent.skills],
                },
                indent=2,
            )
        )
        return 0

    except Exception as e:
        logger.error(f"Get agent failed: {e}")
        return 1


def cmd_agent_update(args: argparse.Namespace) -> int:
    """
    Update an existing agent.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        config_path = Path(args.config)
        if not config_path.exists():
            logger.error(f"Config file not found: {config_path}")
            return 1

        with open(config_path) as f:
            config = json.load(f)

        # Convert skills list of dicts to Skill objects
        # Handle both 'input_schema' and 'parameters' field names
        skills = []
        for skill_data in config.get("skills", []):
            skill_dict = {
                "name": skill_data.get("name", skill_data.get("id", "")),
                "description": skill_data.get("description", ""),
            }
            if "input_schema" in skill_data:
                skill_dict["input_schema"] = skill_data["input_schema"]
            elif "parameters" in skill_data:
                skill_dict["input_schema"] = skill_data["parameters"]
            skills.append(Skill(**skill_dict))
        config["skills"] = skills

        # Convert provider string to enum with validation
        if "provider" in config:
            provider_value = config["provider"].lower()
            provider_map = {
                "anthropic": AgentProvider.ANTHROPIC,
                "custom": AgentProvider.CUSTOM,
                "other": AgentProvider.OTHER,
                "example corp": AgentProvider.CUSTOM,
                "example": AgentProvider.CUSTOM,
            }
            if provider_value in provider_map:
                config["provider"] = provider_map[provider_value]
            else:
                logger.warning(f"Unknown provider '{config['provider']}', using 'custom'")
                config["provider"] = AgentProvider.CUSTOM

        # Convert visibility string to enum if present
        if "visibility" in config:
            try:
                config["visibility"] = AgentVisibility(config["visibility"].lower())
            except ValueError:
                logger.warning(f"Unknown visibility '{config['visibility']}', using 'public'")
                config["visibility"] = AgentVisibility.PUBLIC

        # Handle security_schemes conversion
        if "security_schemes" in config:
            transformed_schemes = {}
            for scheme_name, scheme_data in config["security_schemes"].items():
                scheme_type = scheme_data.get("type", "").lower()
                type_map = {
                    "http": "bearer",
                    "bearer": "bearer",
                    "apikey": "api_key",
                    "api_key": "api_key",
                    "oauth2": "oauth2",
                }
                mapped_type = type_map.get(scheme_type, "bearer")
                transformed_schemes[scheme_name] = {
                    "type": mapped_type,
                    "description": scheme_data.get("description", ""),
                }
            config["security_schemes"] = transformed_schemes

        # Remove fields that aren't in AgentRegistration model
        valid_fields = {
            "name",
            "description",
            "path",
            "url",
            "version",
            "provider",
            "security_schemes",
            "skills",
            "tags",
            "visibility",
            "license",
        }
        config = {k: v for k, v in config.items() if k in valid_fields}

        agent = AgentRegistration(**config)
        client = _create_client(args)
        response = client.update_agent(args.path, agent)

        logger.info(f"Agent updated successfully: {response.name}")
        return 0

    except Exception as e:
        logger.error(f"Agent update failed: {e}")
        logger.debug("Full error details:", exc_info=True)
        return 1


def cmd_agent_delete(args: argparse.Namespace) -> int:
    """
    Delete an agent from the registry.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        if not args.force:
            confirmation = input(f"Delete agent {args.path}? (yes/no): ")
            if confirmation.lower() != "yes":
                logger.info("Operation cancelled")
                return 0

        client = _create_client(args)
        client.delete_agent(args.path)

        logger.info(f"Agent deleted successfully: {args.path}")
        return 0

    except Exception as e:
        logger.error(f"Agent deletion failed: {e}")
        return 1


def cmd_agent_toggle(args: argparse.Namespace) -> int:
    """
    Toggle agent enabled/disabled status.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.toggle_agent(args.path, args.enabled)

        logger.info(f"Agent {response.path} is now {'enabled' if response.is_enabled else 'disabled'}")
        return 0

    except Exception as e:
        logger.error(f"Agent toggle failed: {e}")
        return 1


def cmd_agent_discover(args: argparse.Namespace) -> int:
    """
    Discover agents by required skills.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        skills = [s.strip() for s in args.skills.split(",")]
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else None

        client = _create_client(args)
        response = client.discover_agents_by_skills(skills=skills, tags=tags, max_results=args.max_results)

        if not response.agents:
            logger.info("No agents found matching the required skills")
            return 0

        logger.info(f"Found {len(response.agents)} matching agents:\n")
        for agent in response.agents:
            print(f"{agent.name} ({agent.path})")
            print(f"  Relevance: {agent.relevance_score:.2%}")
            print(f"  Matching skills: {', '.join(agent.matching_skills)}")
            print()

        return 0

    except Exception as e:
        logger.error(f"Agent discovery failed: {e}")
        return 1


def cmd_agent_search(args: argparse.Namespace) -> int:
    """
    Perform semantic search for agents.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.discover_agents_semantic(query=args.query, max_results=args.max_results)

        if not response.agents:
            logger.info("No agents found matching the query")
            return 0

        logger.info(f"Found {len(response.agents)} matching agents:\n")
        for agent in response.agents:
            print(f"{agent.name} ({agent.path})")
            print(f"  Relevance: {agent.relevance_score:.2%}")
            print(f"  {agent.description[:100]}...")
            print()

        return 0

    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        return 1


def cmd_agent_rate(args: argparse.Namespace) -> int:
    """
    Rate an agent (1-5 stars).

    Args:
        args: Command arguments with path and rating

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: RatingResponse = client.rate_agent(path=args.path, rating=args.rating)

        logger.info(f"✓ {response.message}")
        logger.info(f"Average rating: {response.average_rating:.2f} stars")

        return 0

    except Exception as e:
        logger.error(f"Failed to rate agent: {e}")
        return 1


def cmd_agent_rating(args: argparse.Namespace) -> int:
    """
    Get rating information for an agent.

    Args:
        args: Command arguments with path

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: RatingInfoResponse = client.get_agent_rating(path=args.path)

        logger.info(f"\nRating for agent '{args.path}':")
        logger.info(f"  Average: {response.num_stars:.2f} stars")
        logger.info(f"  Total ratings: {len(response.rating_details)}")

        if response.rating_details:
            logger.info("\nIndividual ratings (most recent):")
            # Show first 10 ratings
            for detail in response.rating_details[:10]:
                logger.info(f"  {detail.user}: {detail.rating} stars")

            if len(response.rating_details) > 10:
                logger.info(f"  ... and {len(response.rating_details) - 10} more")

        return 0

    except Exception as e:
        logger.error(f"Failed to get ratings: {e}")
        return 1


def cmd_agent_security_scan(args: argparse.Namespace) -> int:
    """
    Get security scan results for an agent.

    Args:
        args: Command arguments with path and optional json flag

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: AgentSecurityScanResponse = client.get_agent_security_scan(path=args.path)

        # Always output as JSON since the response structure is complex
        print(json.dumps(response.model_dump(), indent=2, default=str))
        return 0

    except Exception as e:
        logger.error(f"Failed to get security scan results: {e}")
        return 1


def cmd_agent_rescan(args: argparse.Namespace) -> int:
    """
    Trigger manual security scan for an agent (admin only).

    Args:
        args: Command arguments with path and optional json flag

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response: AgentRescanResponse = client.rescan_agent(path=args.path)

        if hasattr(args, "json") and args.json:
            # Output raw JSON
            print(json.dumps(response.model_dump(), indent=2, default=str))
        else:
            # Pretty print results
            safety_status = "SAFE" if response.is_safe else "UNSAFE"
            logger.info(f"\nSecurity scan completed for agent '{args.path}':")
            logger.info(f"  Status: {safety_status}")
            logger.info(f"  Scan timestamp: {response.scan_timestamp}")
            logger.info(f"  Analyzers used: {', '.join(response.analyzers_used)}")
            logger.info("\n  Severity counts:")
            logger.info(f"    Critical: {response.critical_issues}")
            logger.info(f"    High: {response.high_severity}")
            logger.info(f"    Medium: {response.medium_severity}")
            logger.info(f"    Low: {response.low_severity}")

            if response.output_file:
                logger.info(f"\n  Output file: {response.output_file}")

            if response.scan_failed:
                logger.error(f"\n  Scan failed: {response.error_message}")
                return 1

            if not response.is_safe:
                logger.warning("\n  WARNING: Agent flagged as potentially unsafe!")

        return 0

    except Exception as e:
        logger.error(f"Failed to trigger security scan: {e}")
        return 1


def cmd_anthropic_list_servers(args: argparse.Namespace) -> int:
    """
    List all servers using Anthropic Registry API v0.1.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        result: AnthropicServerList = client.anthropic_list_servers(limit=args.limit)

        # Print raw JSON if requested
        if args.raw:
            print(json.dumps(result.model_dump(), indent=2, default=str))
            return 0

        logger.info(f"Retrieved {len(result.servers)} servers\n")

        if result.metadata:
            logger.info(f"Next cursor: {result.metadata.nextCursor}")
            logger.info(f"Count: {result.metadata.count}\n")

        # Print server details
        for idx, server_response in enumerate(result.servers, 1):
            server = server_response.server
            print(f"{idx}. {server.name}")
            print(f"   Title: {server.title or 'N/A'}")
            print(f"   Description: {server.description[:100]}...")
            print(f"   Version: {server.version}")
            print(f"   Website: {server.websiteUrl or 'N/A'}")

            if server.repository:
                print(f"   Repository: {server.repository.url}")

            if server.packages:
                print(f"   Packages: {len(server.packages)} package(s)")
            print()

        return 0

    except Exception as e:
        logger.error(f"Failed to list servers: {e}")
        return 1


def cmd_anthropic_list_versions(args: argparse.Namespace) -> int:
    """
    List versions for a specific server using Anthropic Registry API v0.1.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        result: AnthropicServerList = client.anthropic_list_server_versions(server_name=args.server_name)

        # Print raw JSON if requested
        if args.raw:
            print(json.dumps(result.model_dump(), indent=2, default=str))
            return 0

        logger.info(f"Found {len(result.servers)} version(s) for {args.server_name}\n")

        for idx, server_response in enumerate(result.servers, 1):
            server = server_response.server
            print(f"{idx}. Version {server.version}")
            print(f"   Name: {server.name}")
            print(f"   Description: {server.description[:100]}...")
            print()

        return 0

    except Exception as e:
        logger.error(f"Failed to list server versions: {e}")
        return 1


def cmd_anthropic_get_server(args: argparse.Namespace) -> int:
    """
    Get detailed information about a specific server version using Anthropic Registry API v0.1.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        result: AnthropicServerResponse = client.anthropic_get_server_version(
            server_name=args.server_name,
            version=args.version,
        )

        # Print raw JSON if requested
        if args.raw:
            print(json.dumps(result.model_dump(), indent=2, default=str))
            return 0

        server = result.server

        print(f"\nServer: {server.name}")
        print(f"Title: {server.title or 'N/A'}")
        print(f"Version: {server.version}")
        print(f"Description: {server.description}")
        print(f"Website: {server.websiteUrl or 'N/A'}")

        if server.repository:
            print("\nRepository:")
            print(f"  URL: {server.repository.url}")
            print(f"  Source: {server.repository.source}")
            if server.repository.id:
                print(f"  ID: {server.repository.id}")
            if server.repository.subfolder:
                print(f"  Subfolder: {server.repository.subfolder}")

        if server.packages:
            print(f"\nPackages ({len(server.packages)}):")
            for idx, package in enumerate(server.packages, 1):
                print(f"  {idx}. {package.registryType}: {package.identifier}")
                print(f"     Version: {package.version}")
                if package.runtimeHint:
                    print(f"     Runtime: {package.runtimeHint}")

        if server.meta:
            print("\nMetadata:")
            print(json.dumps(server.meta, indent=2))

        if result.meta:
            print("\nRegistry Metadata:")
            print(json.dumps(result.meta, indent=2))

        return 0

    except Exception as e:
        logger.error(f"Failed to get server version: {e}")
        return 1


# User Management Command Handlers (Management API)


def cmd_user_list(args: argparse.Namespace) -> int:
    """
    List Keycloak users.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.list_users(
            search=args.search if hasattr(args, "search") and args.search else None,
            limit=args.limit if hasattr(args, "limit") else 500,
        )

        if not response.users:
            logger.info("No users found")
            return 0

        logger.info(f"Found {response.total} users\n")

        for user in response.users:
            enabled_icon = "✓" if user.enabled else "✗"
            print(f"{enabled_icon} {user.username} (ID: {user.id})")
            print(f"  Email: {user.email or 'N/A'}")
            if user.firstName or user.lastName:
                name = f"{user.firstName or ''} {user.lastName or ''}".strip()
                print(f"  Name: {name}")
            print(f"  Groups: {', '.join(user.groups) if user.groups else 'None'}")
            print(f"  Enabled: {user.enabled}")
            print()

        return 0

    except Exception as e:
        logger.error(f"List users failed: {e}")
        return 1


def cmd_user_create_m2m(args: argparse.Namespace) -> int:
    """
    Create M2M service account.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        groups = [g.strip() for g in args.groups.split(",")]
        client = _create_client(args)
        result = client.create_m2m_account(
            name=args.name,
            groups=groups,
            description=args.description if hasattr(args, "description") and args.description else None,
        )

        logger.info("M2M account created successfully\n")
        print(f"Client ID: {result.client_id}")
        print(f"Groups: {', '.join(result.groups)}")
        print()
        print(
            "IMPORTANT: The client secret was created and must be handled securely. It will not be displayed or logged. Please retrieve the secret from a secure source as per documentation."
        )

        return 0

    except Exception as e:
        logger.error(f"Create M2M account failed: {e}")
        return 1


def cmd_user_create_human(args: argparse.Namespace) -> int:
    """
    Create human user account.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        groups = [g.strip() for g in args.groups.split(",")]
        client = _create_client(args)
        result = client.create_human_user(
            username=args.username,
            email=args.email,
            first_name=args.first_name,
            last_name=args.last_name,
            groups=groups,
            password=args.password if hasattr(args, "password") and args.password else None,
        )

        logger.info("User created successfully\n")
        print(f"Username: {result.username}")
        print(f"User ID: {result.id}")
        print(f"Email: {result.email or 'N/A'}")
        if result.firstName or result.lastName:
            name = f"{result.firstName or ''} {result.lastName or ''}".strip()
            print(f"Name: {name}")
        print(f"Groups: {', '.join(result.groups)}")
        print(f"Enabled: {result.enabled}")

        return 0

    except Exception as e:
        logger.error(f"Create user failed: {e}")
        return 1


def cmd_user_delete(args: argparse.Namespace) -> int:
    """
    Delete a user.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        if not args.force:
            confirmation = input(f"Delete user '{args.username}'? (yes/no): ")
            if confirmation.lower() != "yes":
                logger.info("Operation cancelled")
                return 0

        client = _create_client(args)
        result = client.delete_user(args.username)

        logger.info(f"User '{result.username}' deleted successfully")
        return 0

    except Exception as e:
        logger.error(f"Delete user failed: {e}")
        return 1


def cmd_group_create(args: argparse.Namespace) -> int:
    """
    Create a new IAM group.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        result = client.create_keycloak_group(name=args.name, description=args.description)

        logger.info(f"IAM group created successfully: {result.name}")
        print(f"\nGroup: {result.name}")
        print(f"  ID: {result.id}")
        print(f"  Path: {result.path}")
        if result.attributes:
            print(f"  Attributes: {json.dumps(result.attributes, indent=4)}")

        return 0

    except Exception as e:
        logger.error(f"Create IAM group failed: {e}")
        return 1


def cmd_group_delete(args: argparse.Namespace) -> int:
    """
    Delete an IAM group.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        if not args.force:
            confirmation = input(f"Delete IAM group '{args.name}'? (yes/no): ")
            if confirmation.lower() != "yes":
                logger.info("Operation cancelled")
                return 0

        client = _create_client(args)
        result = client.delete_keycloak_group(name=args.name)

        logger.info(f"IAM group deleted successfully: {result.name}")
        return 0

    except Exception as e:
        logger.error(f"Delete IAM group failed: {e}")
        return 1


def cmd_group_list(args: argparse.Namespace) -> int:
    """
    List IAM groups.

    Args:
        args: Command arguments

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        client = _create_client(args)
        response = client.list_keycloak_iam_groups()

        if not response.groups:
            logger.info("No IAM groups found")
            return 0

        logger.info(f"Found {response.total} IAM groups:\n")

        for group in response.groups:
            print(f"Group: {group.name}")
            print(f"  ID: {group.id}")
            print(f"  Path: {group.path}")
            if group.attributes:
                print(f"  Attributes: {json.dumps(group.attributes, indent=4)}")
            print()

        return 0

    except Exception as e:
        logger.error(f"List IAM groups failed: {e}")
        return 1


def main() -> int:
    """
    Main entry point for the CLI.

    Returns:
        Exit code
    """
    parser = argparse.ArgumentParser(
        description="MCP Gateway Registry Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables (used if command-line options not provided):
  REGISTRY_URL        Registry base URL
  AWS_REGION          AWS region where Keycloak and SSM are deployed
  KEYCLOAK_URL        Keycloak base URL
  CLIENT_NAME         Keycloak client name (default: registry-admin-bot)
  GET_TOKEN_SCRIPT    Path to get-m2m-token.sh script

Examples:
  # Register a server (using environment variables)
  export REGISTRY_URL=https://registry.us-east-1.mycorp.click
  export AWS_REGION=us-east-1
  export KEYCLOAK_URL=https://kc.us-east-1.mycorp.click
  uv run python registry_management.py register --config server-config.json

  # Register a server (using command-line arguments)
  uv run python registry_management.py \\
    --registry-url https://registry.us-east-1.mycorp.click \\
    --aws-region us-east-1 \\
    --keycloak-url https://kc.us-east-1.mycorp.click \\
    register --config server-config.json

  # Register a server (using token file)
  uv run python registry_management.py \\
    --registry-url https://registry.us-east-1.mycorp.click \\
    --token-file /path/to/token.txt \\
    register --config server-config.json

  # List all servers
  uv run python registry_management.py list

  # Toggle server status
  uv run python registry_management.py toggle --path /cloudflare-docs

  # Add server to groups
  uv run python registry_management.py add-to-groups --server my-server --groups finance,analytics
        """,
    )

    parser.add_argument("--registry-url", help="Registry base URL (overrides REGISTRY_URL env var)")

    parser.add_argument("--aws-region", help="AWS region (overrides AWS_REGION env var)")

    parser.add_argument("--keycloak-url", help="Keycloak base URL (overrides KEYCLOAK_URL env var)")

    parser.add_argument("--token-file", help="Path to file containing JWT token (bypasses token script)")

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Register command
    register_parser = subparsers.add_parser("register", help="Register a new server")
    register_parser.add_argument("--config", required=True, help="Path to server configuration JSON file")
    register_parser.add_argument("--overwrite", action="store_true", help="Overwrite if server already exists")

    # List command
    list_parser = subparsers.add_parser("list", help="List all servers")
    list_parser.add_argument("--json", action="store_true", help="Print raw JSON response")

    # Toggle command
    toggle_parser = subparsers.add_parser("toggle", help="Toggle server status")
    toggle_parser.add_argument("--path", required=True, help="Server path to toggle")

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a server")
    remove_parser.add_argument("--path", required=True, help="Server path to remove")
    remove_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # Healthcheck command
    subparsers.add_parser("healthcheck", help="Health check all servers")

    # Add to groups command
    add_groups_parser = subparsers.add_parser("add-to-groups", help="Add server to groups")
    add_groups_parser.add_argument("--server", required=True, help="Server name")
    add_groups_parser.add_argument("--groups", required=True, help="Comma-separated group names")

    # Remove from groups command
    remove_groups_parser = subparsers.add_parser("remove-from-groups", help="Remove server from groups")
    remove_groups_parser.add_argument("--server", required=True, help="Server name")
    remove_groups_parser.add_argument("--groups", required=True, help="Comma-separated group names")

    # Create group command
    create_group_parser = subparsers.add_parser("create-group", help="Create a new group")
    create_group_parser.add_argument("--name", required=True, help="Group name")
    create_group_parser.add_argument("--description", help="Group description")
    create_group_parser.add_argument("--keycloak", action="store_true", help="Also create in Keycloak")

    # Delete group command
    delete_group_parser = subparsers.add_parser("delete-group", help="Delete a group")
    delete_group_parser.add_argument("--name", required=True, help="Group name")
    delete_group_parser.add_argument("--keycloak", action="store_true", help="Also delete from Keycloak")
    delete_group_parser.add_argument(
        "--force", action="store_true", help="Force deletion of system groups and skip confirmation"
    )

    # List groups command
    list_groups_parser = subparsers.add_parser("list-groups", help="List all groups")
    list_groups_parser.add_argument("--no-keycloak", action="store_true", help="Exclude Keycloak information")
    list_groups_parser.add_argument("--no-scopes", action="store_true", help="Exclude scope information")

    # Server rate command
    server_rate_parser = subparsers.add_parser("server-rate", help="Rate a server (1-5 stars)")
    server_rate_parser.add_argument("--path", required=True, help="Server path (e.g., /cloudflare-docs)")
    server_rate_parser.add_argument(
        "--rating", required=True, type=int, choices=[1, 2, 3, 4, 5], help="Rating value (1-5 stars)"
    )

    # Server rating command
    server_rating_parser = subparsers.add_parser("server-rating", help="Get rating information for a server")
    server_rating_parser.add_argument("--path", required=True, help="Server path (e.g., /cloudflare-docs)")

    # Server security scan command
    security_scan_parser = subparsers.add_parser("security-scan", help="Get security scan results for a server")
    security_scan_parser.add_argument("--path", required=True, help="Server path (e.g., /cloudflare-docs)")
    security_scan_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    # Server rescan command
    rescan_parser = subparsers.add_parser("rescan", help="Trigger manual security scan for a server (admin only)")
    rescan_parser.add_argument("--path", required=True, help="Server path (e.g., /cloudflare-docs)")
    rescan_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    # Agent Management Commands

    # Agent register command
    agent_register_parser = subparsers.add_parser("agent-register", help="Register a new A2A agent")
    agent_register_parser.add_argument("--config", required=True, help="Path to agent configuration JSON file")

    # Agent list command
    agent_list_parser = subparsers.add_parser("agent-list", help="List all A2A agents")
    agent_list_parser.add_argument("--query", help="Search query string")
    agent_list_parser.add_argument("--enabled-only", action="store_true", help="Show only enabled agents")
    agent_list_parser.add_argument(
        "--visibility", choices=["public", "private", "internal"], help="Filter by visibility level"
    )

    # Agent get command
    agent_get_parser = subparsers.add_parser("agent-get", help="Get agent details")
    agent_get_parser.add_argument("--path", required=True, help="Agent path (e.g., /code-reviewer)")

    # Agent update command
    agent_update_parser = subparsers.add_parser("agent-update", help="Update an existing agent")
    agent_update_parser.add_argument("--path", required=True, help="Agent path")
    agent_update_parser.add_argument("--config", required=True, help="Path to updated agent configuration JSON file")

    # Agent delete command
    agent_delete_parser = subparsers.add_parser("agent-delete", help="Delete an agent")
    agent_delete_parser.add_argument("--path", required=True, help="Agent path")
    agent_delete_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # Agent toggle command
    agent_toggle_parser = subparsers.add_parser("agent-toggle", help="Toggle agent enabled/disabled status")
    agent_toggle_parser.add_argument("--path", required=True, help="Agent path")
    agent_toggle_parser.add_argument(
        "--enabled", required=True, type=lambda x: x.lower() == "true", help="True to enable, false to disable"
    )

    # Agent discover command
    agent_discover_parser = subparsers.add_parser("agent-discover", help="Discover agents by skills")
    agent_discover_parser.add_argument("--skills", required=True, help="Comma-separated list of required skills")
    agent_discover_parser.add_argument("--tags", help="Comma-separated list of tag filters")
    agent_discover_parser.add_argument(
        "--max-results", type=int, default=10, help="Maximum number of results (default: 10)"
    )

    # Agent search command
    agent_search_parser = subparsers.add_parser("agent-search", help="Semantic search for agents")
    agent_search_parser.add_argument("--query", required=True, help="Natural language search query")
    agent_search_parser.add_argument(
        "--max-results", type=int, default=10, help="Maximum number of results (default: 10)"
    )

    # Agent rate command
    agent_rate_parser = subparsers.add_parser("agent-rate", help="Rate an agent (1-5 stars)")
    agent_rate_parser.add_argument("--path", required=True, help="Agent path (e.g., /code-reviewer)")
    agent_rate_parser.add_argument(
        "--rating", required=True, type=int, choices=[1, 2, 3, 4, 5], help="Rating value (1-5 stars)"
    )

    # Agent rating command
    agent_rating_parser = subparsers.add_parser("agent-rating", help="Get rating information for an agent")
    agent_rating_parser.add_argument("--path", required=True, help="Agent path (e.g., /code-reviewer)")

    # Agent security scan command
    agent_security_scan_parser = subparsers.add_parser(
        "agent-security-scan", help="Get security scan results for an agent"
    )
    agent_security_scan_parser.add_argument("--path", required=True, help="Agent path (e.g., /code-reviewer)")

    # Agent rescan command
    agent_rescan_parser = subparsers.add_parser(
        "agent-rescan", help="Trigger manual security scan for an agent (admin only)"
    )
    agent_rescan_parser.add_argument("--path", required=True, help="Agent path (e.g., /code-reviewer)")
    agent_rescan_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    # Anthropic Registry API Commands

    # Anthropic list servers command
    anthropic_list_parser = subparsers.add_parser(
        "anthropic-list", help="List all servers (Anthropic Registry API v0.1)"
    )
    anthropic_list_parser.add_argument("--limit", type=int, help="Maximum results per page")
    anthropic_list_parser.add_argument("--raw", action="store_true", help="Output raw JSON response")

    # Anthropic list versions command
    anthropic_versions_parser = subparsers.add_parser(
        "anthropic-versions", help="List versions for a server (Anthropic Registry API v0.1)"
    )
    anthropic_versions_parser.add_argument(
        "--server-name", required=True, help="Server name in reverse-DNS format (e.g., 'io.mcpgateway/example-server')"
    )
    anthropic_versions_parser.add_argument("--raw", action="store_true", help="Output raw JSON response")

    # Anthropic get server command
    anthropic_get_parser = subparsers.add_parser(
        "anthropic-get", help="Get server details (Anthropic Registry API v0.1)"
    )
    anthropic_get_parser.add_argument("--server-name", required=True, help="Server name in reverse-DNS format")
    anthropic_get_parser.add_argument("--version", default="latest", help="Server version (default: latest)")
    anthropic_get_parser.add_argument("--raw", action="store_true", help="Output raw JSON response")

    # User Management Commands (Management API)

    # List users command
    user_list_parser = subparsers.add_parser("user-list", help="List Keycloak users")
    user_list_parser.add_argument("--search", help="Search string to filter users")
    user_list_parser.add_argument("--limit", type=int, default=500, help="Maximum number of results (default: 500)")

    # Create M2M account command
    user_m2m_parser = subparsers.add_parser("user-create-m2m", help="Create M2M service account")
    user_m2m_parser.add_argument("--name", required=True, help="Service account name/client ID")
    user_m2m_parser.add_argument("--groups", required=True, help="Comma-separated list of group names")
    user_m2m_parser.add_argument("--description", help="Account description")

    # Create human user command
    user_human_parser = subparsers.add_parser("user-create-human", help="Create human user account")
    user_human_parser.add_argument("--username", required=True, help="Username")
    user_human_parser.add_argument("--email", required=True, help="Email address")
    user_human_parser.add_argument("--first-name", required=True, help="First name")
    user_human_parser.add_argument("--last-name", required=True, help="Last name")
    user_human_parser.add_argument("--groups", required=True, help="Comma-separated list of group names")
    user_human_parser.add_argument("--password", help="Initial password (optional)")

    # Delete user command
    user_delete_parser = subparsers.add_parser("user-delete", help="Delete a user")
    user_delete_parser.add_argument("--username", required=True, help="Username to delete")
    user_delete_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # Create IAM group command
    group_create_parser = subparsers.add_parser("group-create", help="Create a new IAM group")
    group_create_parser.add_argument("--name", required=True, help="Group name")
    group_create_parser.add_argument("--description", help="Group description")

    # Delete IAM group command
    group_delete_parser = subparsers.add_parser("group-delete", help="Delete an IAM group")
    group_delete_parser.add_argument("--name", required=True, help="Group name to delete")
    group_delete_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # List IAM groups command
    subparsers.add_parser("group-list", help="List IAM groups")

    args = parser.parse_args()

    # Enable debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Dispatch to command handler
    if not args.command:
        parser.print_help()
        return 1

    command_handlers = {
        "register": cmd_register,
        "list": cmd_list,
        "toggle": cmd_toggle,
        "remove": cmd_remove,
        "healthcheck": cmd_healthcheck,
        "add-to-groups": cmd_add_to_groups,
        "remove-from-groups": cmd_remove_from_groups,
        "create-group": cmd_create_group,
        "delete-group": cmd_delete_group,
        "list-groups": cmd_list_groups,
        "server-rate": cmd_server_rate,
        "server-rating": cmd_server_rating,
        "security-scan": cmd_security_scan,
        "rescan": cmd_rescan,
        "agent-register": cmd_agent_register,
        "agent-list": cmd_agent_list,
        "agent-get": cmd_agent_get,
        "agent-update": cmd_agent_update,
        "agent-delete": cmd_agent_delete,
        "agent-toggle": cmd_agent_toggle,
        "agent-discover": cmd_agent_discover,
        "agent-search": cmd_agent_search,
        "agent-rate": cmd_agent_rate,
        "agent-rating": cmd_agent_rating,
        "agent-security-scan": cmd_agent_security_scan,
        "agent-rescan": cmd_agent_rescan,
        "anthropic-list": cmd_anthropic_list_servers,
        "anthropic-versions": cmd_anthropic_list_versions,
        "anthropic-get": cmd_anthropic_get_server,
        "user-list": cmd_user_list,
        "user-create-m2m": cmd_user_create_m2m,
        "user-create-human": cmd_user_create_human,
        "user-delete": cmd_user_delete,
        "group-create": cmd_group_create,
        "group-delete": cmd_group_delete,
        "group-list": cmd_group_list,
    }

    handler = command_handlers.get(args.command)
    if not handler:
        logger.error(f"Unknown command: {args.command}")
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
