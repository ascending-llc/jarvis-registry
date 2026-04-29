"""
Pytest configuration and shared fixtures for auth_server tests.
"""

import os
from collections.abc import Generator
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

# Set environment variables BEFORE importing the app
# This ensures settings are loaded with correct values
os.environ["AUTH_SERVER_EXTERNAL_URL"] = "http://localhost:8888"
os.environ["AUTH_SERVER_API_PREFIX"] = "/auth"
os.environ["AUTH_PROVIDER"] = "keycloak"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing"

from registry_pkgs.testing.fixtures import setup_test_rsa_keys

_test_rsa_key = setup_test_rsa_keys()


@pytest.fixture
def jwt_rsa_key() -> rsa.RSAPrivateKey:
    return _test_rsa_key


@pytest.fixture
def auth_server_app():
    """Import and return the auth server FastAPI app."""
    from auth_server.server import app

    return app


@pytest.fixture
def test_client(auth_server_app) -> Generator[TestClient, None, None]:
    """Create a test client for the auth server with mocked MongoDB."""
    # Mock MongoDB initialization to prevent actual connection attempts
    with (
        patch("auth_server.server.init_mongodb"),
        patch("registry_pkgs.database.mongodb.MongoDB.connect_db"),
        TestClient(auth_server_app) as client,
    ):
        yield client


@pytest.fixture
def mock_auth_provider():
    """Mock authentication provider for testing."""
    mock_provider = Mock()
    mock_provider.get_jwks.return_value = {
        "keys": [{"kty": "RSA", "use": "sig", "kid": "test-key-id", "n": "test-modulus", "e": "AQAB"}]
    }

    with patch("auth_server.providers.factory.get_auth_provider", return_value=mock_provider):
        yield mock_provider


@pytest.fixture
def clear_device_storage():
    """Clear device flow, client registration, and authorization code storage before and after each test."""
    from auth_server.core.state import (
        authorization_codes_storage,
        device_codes_storage,
        registered_clients,
        user_codes_storage,
    )

    device_codes_storage.clear()
    user_codes_storage.clear()
    registered_clients.clear()
    authorization_codes_storage.clear()

    yield

    device_codes_storage.clear()
    user_codes_storage.clear()
    registered_clients.clear()
    authorization_codes_storage.clear()


# Test markers
pytest.mark.auth = pytest.mark.auth
pytest.mark.oauth_device = pytest.mark.oauth_device
pytest.mark.well_known = pytest.mark.well_known
pytest.mark.integration = pytest.mark.integration
pytest.mark.unit = pytest.mark.unit
