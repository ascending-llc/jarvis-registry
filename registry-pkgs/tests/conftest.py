"""Pytest configuration and fixtures for packages tests."""

from datetime import UTC, datetime

import pytest
from beanie import PydanticObjectId

from registry_pkgs.testing.fixtures import setup_registry_test_env

setup_registry_test_env()


def pytest_report_teststatus(report, config):
    """
    This pytest configuration suppresses the green dots and yellow skip letters
    for passed and skipped tests. In the age of AI coding agent, we should make
    the outputs of unit test runs that need no action as short as possible to
    avoid polluting the LLM context. When certain tests fail, the output is as
    detailed as without this configuration.
    """
    if report.when == "call" and report.passed:
        # Returns (category, short_letter, verbose_word)
        # Setting short_letter to "" suppresses the green dot
        return ("passed", "", "")
    if report.skipped:
        # Marker-based skips report at "setup"; runtime pytest.skip() reports at "call".
        return ("skipped", "", "")


def pytest_sessionfinish(session):
    """
    Drops the empty line that pytest's terminal reporter prints right before the final summary.
    That line ends the progress-letter line, which is empty when pytest_report_teststatus above
    suppresses every letter. A plain (non-wrapper) hook runs before the reporter's wrapper prints
    it, so this patches the writer for that one call. If a failure letter was printed, the line
    is needed and kept.
    """
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or reporter._tw.width_of_current_line != 0:
        return

    writer = reporter._tw
    original_line = writer.line

    def line(s="", **markup):
        del writer.line  # One-shot: later calls go back to the class method.
        if s:
            original_line(s, **markup)

    writer.line = line


@pytest.fixture
def sample_server_data():
    """Sample MCP server data matching ExtendedMCPServer structure."""
    return {
        "serverName": "test-server",
        "config": {
            "title": "Test MCP Server",
            "description": "Test server for unit tests",
            "type": "streamable-http",
            "url": "http://test-server:8000",
            "apiKey": {
                "key": "test_api_key",
                "source": "env",
                "authorization_type": "bearer",
            },
            "requiresOAuth": False,
            "capabilities": '{"experimental": {}}',
            "tools": "test_tool1, test_tool2",
            "toolFunctions": {
                "test_tool1": {
                    "type": "function",
                    "function": {
                        "name": "test_tool1",
                        "description": "Test tool 1",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            },
            "initDuration": 150,
        },
        "author": PydanticObjectId(),
        "path": "/mcp/test",
        "tags": ["test", "demo"],
        "status": "active",
        "numTools": 2,
        "numStars": 0,
    }


@pytest.fixture
def sample_oauth_server_data():
    """Sample OAuth-enabled MCP server data."""
    return {
        "serverName": "oauth-test-server",
        "config": {
            "title": "OAuth Test Server",
            "description": "Server with OAuth configuration",
            "type": "streamable-http",
            "url": "http://oauth-server:8000",
            "requiresOAuth": True,
            "oauth": {
                "client_id": "test_client_id",
                "authorization_url": "https://oauth.example.com/authorize",
                "token_url": "https://oauth.example.com/token",
                "scopes": ["read", "write"],
            },
            "capabilities": "{}",
            "tools": "oauth_tool",
            "toolFunctions": {},
            "initDuration": 200,
        },
        "author": PydanticObjectId(),
        "path": "/mcp/oauth-test",
        "tags": ["oauth", "test"],
        "status": "active",
        "numTools": 1,
        "numStars": 5,
    }


@pytest.fixture
def sample_token_data():
    """Sample token data for testing."""
    return {
        "type": "oauth_access",
        "identifier": "github",
        "user_id": str(PydanticObjectId()),
        "encrypted_value": "encrypted_token_value",
        "expires_at": datetime.now(UTC),
        "metadata": {
            "provider": "github",
            "scopes": ["repo", "user"],
        },
    }
