"""Integration tests for the downstream OAuth discovery documents:
- protected resource metadata branching (RFC 9728)
- per-server authorization server metadata (RFC 8414)
"""

from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from auth_server.core.config import settings
from auth_server.deps import get_downstream_token_check
from registry_pkgs.core.downstream_oauth import downstream_mcp_issuer

USER_ID = "507f1f77bcf86cd799439011"
_PRM_BASE = f"/.well-known/oauth-protected-resource{settings.service_base_path}/proxy"


@pytest.fixture
def token_check_override(auth_server_app) -> Generator[AsyncMock, None, None]:
    """Override the downstream token check with a controllable async mock."""
    mock = AsyncMock()
    auth_server_app.dependency_overrides[get_downstream_token_check] = lambda: _Holder(mock)
    yield mock
    auth_server_app.dependency_overrides.pop(get_downstream_token_check, None)


class _Holder:
    """Wraps the mock so .has_valid_downstream_token resolves to it."""

    def __init__(self, mock: AsyncMock):
        self.has_valid_downstream_token = mock


def test_prm_direct_connect_without_tokens_points_to_downstream_issuer(test_client: TestClient, token_check_override):
    token_check_override.return_value = False
    resp = test_client.get(f"{_PRM_BASE}/server/{USER_ID}/github")
    assert resp.status_code == 200
    body = resp.json()
    expected_issuer = downstream_mcp_issuer(settings.jwt_issuer, USER_ID, "github")
    assert body["authorization_servers"] == [expected_issuer]


def test_prm_direct_connect_with_tokens_points_to_registry_issuer(test_client: TestClient, token_check_override):
    token_check_override.return_value = True
    resp = test_client.get(f"{_PRM_BASE}/server/{USER_ID}/github")
    assert resp.status_code == 200
    assert resp.json()["authorization_servers"] == [settings.jwt_issuer]


def test_prm_non_direct_connect_unchanged(test_client: TestClient, token_check_override):
    # mcpgw/mcp (and any non server/ path) always points to the standard registry issuer.
    resp = test_client.get(f"{_PRM_BASE}/mcpgw/mcp")
    assert resp.status_code == 200
    assert resp.json()["authorization_servers"] == [settings.jwt_issuer]
    token_check_override.assert_not_awaited()


def test_downstream_as_metadata_issuer_and_endpoints(test_client: TestClient):
    resp = test_client.get(f"/.well-known/oauth-authorization-server/proxy/server/oauth/{USER_ID}/github")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == downstream_mcp_issuer(settings.jwt_issuer, USER_ID, "github")
    assert body["authorization_endpoint"].endswith(f"/downstream/oauth/authorize/{USER_ID}/github")
    assert body["token_endpoint"].endswith(f"/downstream/oauth/token/{USER_ID}/github")
    assert body["jwks_uri"] == f"{settings.jwt_issuer}/.well-known/jwks.json"
    assert body["code_challenge_methods_supported"] == ["S256"]
