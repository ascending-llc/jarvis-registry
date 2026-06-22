"""
Pytest configuration and shared fixtures for auth_server tests.
"""

import os
from collections.abc import Generator
from unittest.mock import AsyncMock, Mock, patch

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


def _seed_default_oauth_state() -> None:
    from tests.support.oauth_state_store import test_oauth_state_store

    test_oauth_state_store.clear()
    test_oauth_state_store.save_client(
        "test-client",
        {
            "client_id": "test-client",
            "client_secret": "test-secret",
            "client_name": "Default Test Client",
            "redirect_uris": [
                "http://localhost/callback",
                "https://example.com/callback",
                "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback",
            ],
            "grant_types": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:device_code"],
            "response_types": ["code"],
            "scope": "servers-read agents-read",
            "token_endpoint_auth_method": "none",
            "registered_at": 0,
            "ip_address": "127.0.0.1",
        },
    )


@pytest.fixture
def jwt_rsa_key() -> rsa.RSAPrivateKey:
    return _test_rsa_key


@pytest.fixture
def mock_redis_client() -> Mock:
    """Mock Redis client used by auth-server lifespan tests."""
    return Mock()


@pytest.fixture(autouse=True)
def mock_auth_server_infra(mock_redis_client: Mock) -> Generator[None, None, None]:
    """Mock external infrastructure so tests do not require MongoDB or Redis."""
    with (
        patch("auth_server.server.init_mongodb", new_callable=AsyncMock),
        patch("auth_server.server.close_mongodb", new_callable=AsyncMock),
        patch("auth_server.server.create_redis_client", return_value=mock_redis_client),
        patch("auth_server.server.close_redis_client"),
    ):
        yield


@pytest.fixture
def auth_server_app():
    """Import and return the auth server FastAPI app."""
    from auth_server.deps import get_oauth_state_store
    from auth_server.server import app
    from tests.support.oauth_state_store import test_oauth_state_store

    app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
    return app


@pytest.fixture
def test_client(auth_server_app) -> Generator[TestClient, None, None]:
    """Create a test client for the auth server."""
    _seed_default_oauth_state()
    with TestClient(auth_server_app) as client:
        yield client
    from tests.support.oauth_state_store import test_oauth_state_store

    test_oauth_state_store.clear()


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
    """Clear device flow, client registration, authorization code, and refresh token storage before and after each test."""
    from tests.support.oauth_state_store import test_oauth_state_store

    _seed_default_oauth_state()

    yield

    test_oauth_state_store.clear()


# Test markers
pytest.mark.auth = pytest.mark.auth
pytest.mark.oauth_device = pytest.mark.oauth_device
pytest.mark.well_known = pytest.mark.well_known
pytest.mark.integration = pytest.mark.integration
pytest.mark.unit = pytest.mark.unit
