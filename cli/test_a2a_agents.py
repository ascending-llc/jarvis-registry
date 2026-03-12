#!/usr/bin/env python3
"""
Test A2A Agents Public API Endpoints.

This script tests the A2A Agents API endpoints using JWT tokens
generated from the MCP Registry UI or credentials provider.

Usage:
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --test list-agents
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --test get-agent --agent-name test-agent
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --test pagination-flow
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --test all --verbose
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --base-url http://localhost --debug

Note: Tokens have a short lifetime for security. If your token expires, generate a new one
from the UI or ask your administrator to increase the access token timeout in Keycloak.
"""

import argparse
import base64
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
AGENTS_API_VERSION: str = settings.anthropic_api_version


class TestResult:
    """Container for test results."""

    def __init__(self, test_name: str) -> None:
        """Initialize test result."""
        self.test_name = test_name
        self.passed = False
        self.duration_ms = 0
        self.response = None
        self.error = None
        self.message = ""


def _check_token_expiration(access_token: str) -> None:
    """
    Check if JWT token is expired and warn if expiring soon.

    Args:
        access_token: JWT access token to check
    """
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            logger.warning("Invalid JWT format, cannot check expiration")
            return

        payload = parts[1]
        padding = len(payload) % 4
        if padding:
            payload += "=" * (4 - padding)

        decoded = base64.urlsafe_b64decode(payload)
        token_data = json.loads(decoded)

        exp = token_data.get("exp")
        if not exp:
            logger.warning("Token does not have expiration field")
            return

        exp_dt = datetime.fromtimestamp(exp, tz=UTC)
        now = datetime.now(UTC)
        time_until_expiry = exp_dt - now

        if time_until_expiry.total_seconds() < 0:
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
        elif time_until_expiry.total_seconds() < 120:
            seconds = int(time_until_expiry.total_seconds())
            logger.warning(
                f"WARNING: Token will expire in {seconds} seconds at {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        else:
            remaining_seconds = int(time_until_expiry.total_seconds())
            logger.info(
                f"Token is valid until {exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} ({remaining_seconds} seconds remaining)"
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


def _make_api_request(
    endpoint: str, access_token: str, base_url: str, method: str = "GET", params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """
    Make an API request to the A2A Agents API.

    Args:
        endpoint: API endpoint
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
        logger.debug(f"Making {method} request to: {url}")
        response = requests.request(method=method, url=url, headers=headers, params=params, timeout=10)

        if response.status_code == 401:
            logger.warning("Received 401 Unauthorized")
            return None

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        logger.debug(f"API request failed: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.debug(f"Response status: {e.response.status_code}")
            logger.debug(f"Response body: {e.response.text}")
        return None


def _format_json_output(data: Any, verbose: bool = False) -> str:
    """
    Format JSON output for display.

    Args:
        data: Data to format
        verbose: Whether to show full output

    Returns:
        Formatted JSON string
    """
    if verbose:
        return json.dumps(data, indent=2)
    return json.dumps(data, indent=2)[:200] + ("..." if len(json.dumps(data)) > 200 else "")


def _print_test_result(result: TestResult, verbose: bool = False) -> None:
    """
    Print formatted test result.

    Args:
        result: Test result object
        verbose: Whether to show full output
    """
    status = "PASS" if result.passed else "FAIL"
    print(f"[TEST] {result.test_name}: {status} ({result.duration_ms}ms)")

    if result.message:
        print(f"       {result.message}")

    if verbose and result.response:
        print(f"       Response: {_format_json_output(result.response, verbose=True)}")

    if result.error:
        print(f"       Error: {result.error}")

    print()


def _test_list_agents(access_token: str, base_url: str, limit: int = 10) -> TestResult:
    """
    Test listing agents endpoint.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        limit: Number of agents to list

    Returns:
        Test result object
    """
    result = TestResult("list-agents")
    start_time = time.time()

    endpoint = f"/{AGENTS_API_VERSION}/agents"
    response = _make_api_request(
        endpoint=endpoint, access_token=access_token, base_url=base_url, params={"limit": limit}
    )

    result.duration_ms = int((time.time() - start_time) * 1000)

    if response:
        result.response = response
        result.passed = True
        agents = response.get("agents", [])
        next_cursor = response.get("metadata", {}).get("nextCursor")
        result.message = f"{len(agents)} agents returned"
        if next_cursor:
            result.message += f", nextCursor={next_cursor}"
    else:
        result.error = "Failed to list agents"

    return result


def _test_list_agents_paginated(access_token: str, base_url: str, limit: int = 3) -> TestResult:
    """
    Test pagination endpoint.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        limit: Number of agents per page

    Returns:
        Test result object
    """
    result = TestResult("list-agents-paginated")
    start_time = time.time()

    endpoint = f"/{AGENTS_API_VERSION}/agents"
    response = _make_api_request(
        endpoint=endpoint, access_token=access_token, base_url=base_url, params={"limit": limit}
    )

    result.duration_ms = int((time.time() - start_time) * 1000)

    if response:
        result.response = response
        result.passed = True
        agents = response.get("agents", [])
        next_cursor = response.get("metadata", {}).get("nextCursor")
        result.message = f"Page 1: {len(agents)} agents"
        if next_cursor:
            result.message += ", nextCursor available"
    else:
        result.error = "Failed to list agents"

    return result


def _test_get_agent(access_token: str, base_url: str, agent_name: str) -> TestResult:
    """
    Test getting specific agent endpoint.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        agent_name: Agent name (URL-encoded or plain)

    Returns:
        Test result object
    """
    result = TestResult(f"get-agent ({agent_name})")
    start_time = time.time()

    encoded_name = quote(agent_name, safe="")
    endpoint = f"/{AGENTS_API_VERSION}/agents/{encoded_name}"
    response = _make_api_request(endpoint=endpoint, access_token=access_token, base_url=base_url)

    result.duration_ms = int((time.time() - start_time) * 1000)

    if response:
        result.response = response
        result.passed = True
        agent_data = response.get("agent", {})
        name = agent_data.get("name", agent_name)
        description = agent_data.get("description", "")[:50]
        result.message = f"Agent name={name}"
        if description:
            result.message += f", desc={description}..."
    else:
        result.error = "Failed to get agent"

    return result


def _test_get_agent_versions(access_token: str, base_url: str, agent_name: str) -> TestResult:
    """
    Test getting agent versions endpoint.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        agent_name: Agent name (URL-encoded or plain)

    Returns:
        Test result object
    """
    result = TestResult(f"get-agent-versions ({agent_name})")
    start_time = time.time()

    encoded_name = quote(agent_name, safe="")
    endpoint = f"/{AGENTS_API_VERSION}/agents/{encoded_name}/versions"
    response = _make_api_request(endpoint=endpoint, access_token=access_token, base_url=base_url)

    result.duration_ms = int((time.time() - start_time) * 1000)

    if response:
        result.response = response
        result.passed = True
        versions = response.get("versions", [])
        result.message = f"{len(versions)} versions found"
    else:
        result.error = "Failed to get agent versions"

    return result


def _test_pagination_flow(access_token: str, base_url: str) -> TestResult:
    """
    Test full pagination flow through pages.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API

    Returns:
        Test result object
    """
    result = TestResult("pagination-flow")
    start_time = time.time()

    endpoint = f"/{AGENTS_API_VERSION}/agents"
    all_agents = []
    cursor = None
    page_count = 0
    max_pages = 5

    try:
        while page_count < max_pages:
            params = {"limit": 3}
            if cursor:
                params["cursor"] = cursor

            response = _make_api_request(endpoint=endpoint, access_token=access_token, base_url=base_url, params=params)

            if not response:
                result.error = "Failed to fetch page"
                break

            agents = response.get("agents", [])
            all_agents.extend(agents)
            page_count += 1

            cursor = response.get("metadata", {}).get("nextCursor")
            if not cursor:
                break

        result.duration_ms = int((time.time() - start_time) * 1000)

        if all_agents:
            result.response = {"agents": all_agents[:3], "total_collected": len(all_agents)}
            result.passed = True
            result.message = f"Collected {len(all_agents)} agents across {page_count} pages"
        else:
            result.error = "No agents found"

    except Exception as e:
        result.error = str(e)

    return result


def _test_error_invalid_token(base_url: str) -> TestResult:
    """
    Test error handling with invalid token.

    Args:
        base_url: Base URL for the API

    Returns:
        Test result object
    """
    result = TestResult("error-invalid-token")
    start_time = time.time()

    endpoint = f"/{AGENTS_API_VERSION}/agents"
    url = f"{base_url}{endpoint}"
    headers = {"X-Authorization": "Bearer invalid_token_here", "Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        result.duration_ms = int((time.time() - start_time) * 1000)

        if response.status_code == 401:
            result.passed = True
            result.message = "Correctly returned 401 Unauthorized"
            result.response = response.json() if response.text else {}
        else:
            result.error = f"Expected 401, got {response.status_code}"

    except requests.exceptions.RequestException as e:
        result.error = str(e)

    return result


def _test_error_missing_agent(access_token: str, base_url: str) -> TestResult:
    """
    Test error handling with non-existent agent.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API

    Returns:
        Test result object
    """
    result = TestResult("error-missing-agent")
    start_time = time.time()

    endpoint = f"/{AGENTS_API_VERSION}/agents/non-existent-agent-xyz-123"
    url = f"{base_url}{endpoint}"
    headers = {"X-Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        result.duration_ms = int((time.time() - start_time) * 1000)

        if response.status_code == 404:
            result.passed = True
            result.message = "Correctly returned 404 Not Found"
            result.response = response.json() if response.text else {}
        else:
            result.error = f"Expected 404, got {response.status_code}"

    except requests.exceptions.RequestException as e:
        result.error = str(e)

    return result


def _run_all_tests(
    access_token: str, base_url: str, agent_name: str | None = None, verbose: bool = False
) -> list[TestResult]:
    """
    Run all API tests.

    Args:
        access_token: JWT access token
        base_url: Base URL for the API
        agent_name: Optional agent name for specific tests
        verbose: Show verbose output

    Returns:
        List of test results
    """
    logger.info("Running all API tests...")
    results = []

    results.append(_test_list_agents(access_token, base_url, limit=10))
    _print_test_result(results[-1], verbose)

    time.sleep(0.5)

    results.append(_test_list_agents_paginated(access_token, base_url, limit=3))
    _print_test_result(results[-1], verbose)

    time.sleep(0.5)

    results.append(_test_pagination_flow(access_token, base_url))
    _print_test_result(results[-1], verbose)

    time.sleep(0.5)

    if agent_name:
        results.append(_test_get_agent(access_token, base_url, agent_name))
        _print_test_result(results[-1], verbose)

        time.sleep(0.5)

        results.append(_test_get_agent_versions(access_token, base_url, agent_name))
        _print_test_result(results[-1], verbose)

        time.sleep(0.5)

    results.append(_test_error_invalid_token(base_url))
    _print_test_result(results[-1], verbose)

    time.sleep(0.5)

    results.append(_test_error_missing_agent(access_token, base_url))
    _print_test_result(results[-1], verbose)

    return results


def _print_summary(results: list[TestResult]) -> None:
    """
    Print test summary report.

    Args:
        results: List of test results
    """
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    status = "ALL PASSED" if passed == total else f"{passed}/{total} PASSED"

    print("=" * 80)
    print(f"[SUMMARY] {status}")
    print("=" * 80)
    for result in results:
        status_str = "PASS" if result.passed else "FAIL"
        print(f"  {result.test_name:<40} {status_str:<8} {result.duration_ms}ms")


def _parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description=f"Test A2A Agents API {AGENTS_API_VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --test list-agents
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --test get-agent --agent-name test-agent
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --test pagination-flow --verbose
    uv run python cli/test_a2a_agents.py --token-file .oauth-tokens/ingress.json --base-url https://api.example.com --debug

Note: If your token expires, generate a new one from the UI. Administrators can increase
token lifetime in Keycloak: Realm Settings → Tokens → Access Token Lifespan
""",
    )

    parser.add_argument(
        "--token-file", type=str, required=True, help="Path to token JSON file (e.g., .oauth-tokens/ingress.json)"
    )

    parser.add_argument(
        "--base-url", type=str, default=DEFAULT_BASE_URL, help=f"Base URL for API (default: {DEFAULT_BASE_URL})"
    )

    parser.add_argument(
        "--test",
        type=str,
        choices=[
            "all",
            "list-agents",
            "list-agents-paginated",
            "get-agent",
            "get-agent-versions",
            "pagination-flow",
            "error-invalid-token",
            "error-missing-agent",
        ],
        default="all",
        help="Which test to run (default: all)",
    )

    parser.add_argument("--agent-name", type=str, help="Agent name for get-agent or get-agent-versions tests")

    parser.add_argument("--verbose", action="store_true", help="Show detailed output including full responses")

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    return parser.parse_args()


def _execute_test(
    test_name: str, access_token: str, base_url: str, agent_name: str | None, verbose: bool
) -> list[TestResult]:
    """
    Execute a single test based on test name.

    Args:
        test_name: Name of test to execute
        access_token: JWT access token
        base_url: Base URL for API
        agent_name: Optional agent name
        verbose: Verbose output flag

    Returns:
        List of test results
    """
    results = []

    if test_name == "all":
        results = _run_all_tests(access_token, base_url, agent_name, verbose)
    elif test_name == "list-agents":
        result = _test_list_agents(access_token, base_url)
        results.append(result)
        _print_test_result(result, verbose)
    elif test_name == "list-agents-paginated":
        result = _test_list_agents_paginated(access_token, base_url)
        results.append(result)
        _print_test_result(result, verbose)
    elif test_name == "get-agent":
        if not agent_name:
            logger.error("--agent-name required for get-agent test")
            sys.exit(1)
        result = _test_get_agent(access_token, base_url, agent_name)
        results.append(result)
        _print_test_result(result, verbose)
    elif test_name == "get-agent-versions":
        if not agent_name:
            logger.error("--agent-name required for get-agent-versions test")
            sys.exit(1)
        result = _test_get_agent_versions(access_token, base_url, agent_name)
        results.append(result)
        _print_test_result(result, verbose)
    elif test_name == "pagination-flow":
        result = _test_pagination_flow(access_token, base_url)
        results.append(result)
        _print_test_result(result, verbose)
    elif test_name == "error-invalid-token":
        result = _test_error_invalid_token(base_url)
        results.append(result)
        _print_test_result(result, verbose)
    elif test_name == "error-missing-agent":
        result = _test_error_missing_agent(access_token, base_url)
        results.append(result)
        _print_test_result(result, verbose)

    return results


def main():
    """Main entry point."""
    args = _parse_arguments()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 80)
    logger.info(f"A2A Agents API {AGENTS_API_VERSION} Test Tool")
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

    _check_token_expiration(access_token)

    results = _execute_test(args.test, access_token, args.base_url, args.agent_name, args.verbose)

    if results:
        _print_summary(results)


if __name__ == "__main__":
    main()
