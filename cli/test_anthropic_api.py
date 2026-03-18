#!/usr/bin/env python3
"""
DEPRECATED: This script is deprecated in favor of the Registry Management API.

Use instead:
    uv run python api/registry_management.py anthropic-list --help
    uv run python api/registry_management.py anthropic-get --help

See api/README.md for full documentation.

Test Anthropic MCP Registry API.

This script tests the Anthropic MCP Registry API endpoints using JWT tokens
generated from the MCP Registry UI.

Usage:
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/mcp-registry-api-tokens-2025-10-12.json
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/ingress.json --base-url http://localhost
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/ingress.json --test list-servers
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/ingress.json --test get-server --server-name io.mcpgateway/atlassian

Note: Tokens have a short lifetime for security. If your token expires, generate a new one
from the UI or ask your administrator to increase the access token timeout in Keycloak.
"""

print("=" * 80)
print("WARNING: This script is DEPRECATED.")
print("Please use the Registry Management API instead:")
print("  uv run python api/registry_management.py anthropic-list --help")
print("  uv run python api/registry_management.py anthropic-get --help")
print("See api/README.md for full documentation.")
print("=" * 80)
print()

import argparse
import base64
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

# Add project root to path to import application settings
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from registry.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)


DEFAULT_BASE_URL: str = "http://localhost"


def _check_token_expiration(access_token: str) -> None:
    """
    Check if JWT token is expired and exit with informative message if so.

    Args:
        access_token: JWT access token to check

    Exits:
        If token is expired or will expire soon
    """
    try:
        # Decode JWT payload (without verification, just to check expiry)
        parts = access_token.split(".")
        if len(parts) != 3:
            logger.warning("Invalid JWT format, cannot check expiration")
            return

        # Decode payload
        payload = parts[1]
        # Add padding if needed
        padding = len(payload) % 4
        if padding:
            payload += "=" * (4 - padding)

        decoded = base64.urlsafe_b64decode(payload)
        token_data = json.loads(decoded)

        # Check expiration
        exp = token_data.get("exp")
        if not exp:
            logger.warning("Token does not have expiration field")
            return

        exp_dt = datetime.fromtimestamp(exp, tz=UTC)
        now = datetime.now(UTC)
        time_until_expiry = exp_dt - now

        if time_until_expiry.total_seconds() < 0:
            # Token is expired
            logger.error("=" * 80)
            logger.error("TOKEN EXPIRED")
            logger.error("=" * 80)
            logger.error(f"Token expired at: {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            logger.error(f"Current time is: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            logger.error(f"Token expired {abs(time_until_expiry.total_seconds())} seconds ago")
            logger.error("")
            logger.error("Please regenerate your token:")
            logger.error("  ./credentials-provider/generate_creds.sh")
            logger.error("=" * 80)
            sys.exit(1)
        elif time_until_expiry.total_seconds() < 60:
            # Token expires soon
            logger.warning(
                f"Token will expire in {int(time_until_expiry.total_seconds())} seconds at {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        else:
            logger.info(
                f"Token is valid until {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({int(time_until_expiry.total_seconds())} seconds remaining)"
            )

    except Exception as e:
        logger.warning(f"Could not check token expiration: {e}")


def _load_token_file(token_file_path: Path) -> dict[str, Any]:
    """
    Load token data from JSON file.

    Args:
        token_file_path: Path to token JSON file

    Returns:
        Token data dictionary
    """
    try:
        with open(token_file_path) as f:
            token_data = json.load(f)
        logger.info(f"Loaded token file: {token_file_path}")
        return token_data
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load token file: {e}")
        sys.exit(1)


def _save_token_file(token_file_path: Path, token_data: dict[str, Any]) -> None:
    """
    Save updated token data to JSON file.

    Args:
        token_file_path: Path to token JSON file
        token_data: Token data dictionary
    """
    try:
        with open(token_file_path, "w") as f:
            json.dump(token_data, f, indent=2)
        logger.info(f"Saved updated tokens to: {token_file_path}")
    except OSError as e:
        logger.error(f"Failed to save token file: {e}")


def _make_api_request(
    endpoint: str, access_token: str, base_url: str, method: str = "GET", params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """
    Make an API request to the Anthropic MCP Registry API.

    Args:
        endpoint: API endpoint (e.g., /{ANTHROPIC_API_VERSION}/servers)
        access_token: JWT access token
        base_url: Base URL for the API
        method: HTTP method
        params: Query parameters

    Returns:
        Response JSON or None if request fails
    """
    url = f"{base_url}{endpoint}"

    headers = {"X-Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    try:
        logger.info(f"Making {method} request to: {url}")
        response = requests.request(method=method, url=url, headers=headers, params=params, timeout=10)

        if response.status_code == 401:
            logger.warning("Received 401 Unauthorized - token may be expired")
            return None

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
        return None


def _test_list_servers(access_token: str, base_url: str, limit: int = 5) -> None:
    """
    Test listing servers endpoint.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        limit: Number of servers to list
    """
    logger.info(f"Testing: List servers (limit={limit})")

    result = _make_api_request(
        endpoint=f"/{settings.anthropic_api_version}/servers",
        access_token=access_token,
        base_url=base_url,
        params={"limit": limit},
    )

    if result:
        print("\n" + "=" * 80)
        print("LIST SERVERS RESPONSE:")
        print("=" * 80)
        print(json.dumps(result, indent=2))
        print("=" * 80 + "\n")

        servers = result.get("servers", [])
        logger.info(f"Found {len(servers)} servers")
    else:
        logger.error("Failed to list servers")


def _test_get_server_versions(access_token: str, base_url: str, server_name: str) -> None:
    """
    Test getting server versions endpoint.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        server_name: Server name (e.g., io.mcpgateway/atlassian)
    """
    logger.info(f"Testing: Get server versions for {server_name}")

    encoded_name = server_name.replace("/", "%2F")
    endpoint = f"/{settings.anthropic_api_version}/servers/{encoded_name}/versions"

    result = _make_api_request(endpoint=endpoint, access_token=access_token, base_url=base_url)

    if result:
        print("\n" + "=" * 80)
        print(f"SERVER VERSIONS RESPONSE: {server_name}")
        print("=" * 80)
        print(json.dumps(result, indent=2))
        print("=" * 80 + "\n")
    else:
        logger.error(f"Failed to get versions for {server_name}")


def _test_get_server_version_details(
    access_token: str, base_url: str, server_name: str, version: str = "latest"
) -> None:
    """
    Test getting server version details endpoint.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        server_name: Server name (e.g., io.mcpgateway/atlassian)
        version: Version (default: latest)
    """
    logger.info(f"Testing: Get server version details for {server_name} v{version}")

    encoded_name = server_name.replace("/", "%2F")
    endpoint = f"/{settings.anthropic_api_version}/servers/{encoded_name}/versions/{version}"

    result = _make_api_request(endpoint=endpoint, access_token=access_token, base_url=base_url)

    if result:
        print("\n" + "=" * 80)
        print(f"SERVER VERSION DETAILS: {server_name} v{version}")
        print("=" * 80)
        print(json.dumps(result, indent=2))
        print("=" * 80 + "\n")
    else:
        logger.error(f"Failed to get version details for {server_name}")


def _run_all_tests(access_token: str, base_url: str) -> None:
    """
    Run all API tests.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
    """
    logger.info("Running all API tests...")

    _test_list_servers(access_token, base_url, limit=10)

    time.sleep(1)

    _test_get_server_versions(access_token, base_url, "io.mcpgateway/atlassian")

    time.sleep(1)

    _test_get_server_version_details(access_token, base_url, "io.mcpgateway/atlassian", "latest")

    logger.info("All tests completed")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=f"Test Anthropic MCP Registry API {settings.anthropic_api_version}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run all tests with default settings
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/mcp-registry-api-tokens-2025-10-12.json

    # Test specific endpoint
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/ingress.json --test list-servers

    # Get server details
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/ingress.json --test get-server --server-name io.mcpgateway/atlassian

    # Custom base URL
    uv run python cli/test_anthropic_api.py --token-file .oauth-tokens/ingress.json --base-url https://mcpgateway.ddns.net

Note: If your token expires, generate a new one from the UI. Administrators can increase
token lifetime in Keycloak: Realm Settings → Tokens → Access Token Lifespan
""",
    )

    parser.add_argument(
        "--token-file",
        type=str,
        required=True,
        help="Path to token JSON file (e.g., .oauth-tokens/mcp-registry-api-tokens-2025-10-12.json)",
    )

    parser.add_argument(
        "--base-url", type=str, default=DEFAULT_BASE_URL, help=f"Base URL for API (default: {DEFAULT_BASE_URL})"
    )

    parser.add_argument(
        "--test",
        type=str,
        choices=["all", "list-servers", "get-versions", "get-server"],
        default="all",
        help="Which test to run (default: all)",
    )

    parser.add_argument(
        "--server-name",
        type=str,
        help="Server name for get-versions or get-server tests (e.g., io.mcpgateway/atlassian)",
    )

    parser.add_argument("--limit", type=int, default=5, help="Number of servers to list (default: 5)")

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 80)
    logger.info(f"Anthropic MCP Registry API {settings.anthropic_api_version} Test Tool")
    logger.info("=" * 80)

    token_file_path = Path(args.token_file)
    if not token_file_path.exists():
        logger.error(f"Token file not found: {token_file_path}")
        sys.exit(1)

    token_data = _load_token_file(token_file_path)

    access_token = None

    if "tokens" in token_data:
        access_token = token_data["tokens"].get("access_token")
    else:
        access_token = token_data.get("access_token")

    if not access_token:
        logger.error("No access_token found in token file")
        sys.exit(1)

    logger.info("Access token loaded successfully")
    logger.info(f"Base URL: {args.base_url}")

    # Check token expiration before making any API calls
    _check_token_expiration(access_token)

    if args.test == "all":
        _run_all_tests(access_token, args.base_url)
    elif args.test == "list-servers":
        _test_list_servers(access_token, args.base_url, args.limit)
    elif args.test == "get-versions":
        if not args.server_name:
            logger.error("--server-name required for get-versions test")
            sys.exit(1)
        _test_get_server_versions(access_token, args.base_url, args.server_name)
    elif args.test == "get-server":
        if not args.server_name:
            logger.error("--server-name required for get-server test")
            sys.exit(1)
        _test_get_server_version_details(access_token, args.base_url, args.server_name, "latest")

    # Note: Tokens have a short lifetime for security. If your token expires,
    # generate a new one from the UI or ask your administrator to increase
    # the access token timeout in Keycloak (Realm Settings → Tokens → Access Token Lifespan)


if __name__ == "__main__":
    main()
