"""Integration tests for the downstream OAuth discovery documents:
- protected resource metadata branching (RFC 9728)
- per-server authorization server metadata (RFC 8414)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from auth_server.core.config import settings
from auth_server.deps import get_server_service
from registry_pkgs.core.downstream_oauth import downstream_mcp_issuer

USER_ID = "507f1f77bcf86cd799439011"
_PRM_BASE = f"/.well-known/oauth-protected-resource{settings.service_base_path}/proxy"


@pytest.fixture
def mock_server_service(auth_server_app):
    """Override get_server_service so tests don't hit MongoDB. Default: server does not require OAuth."""
    svc = MagicMock()
    svc.requires_oauth = AsyncMock(return_value=False)
    auth_server_app.dependency_overrides[get_server_service] = lambda: svc
    yield svc
    auth_server_app.dependency_overrides.pop(get_server_service, None)


def test_prm_direct_connect_points_to_downstream_issuer(test_client: TestClient, mock_server_service):
    mock_server_service.requires_oauth = AsyncMock(return_value=True)
    resp = test_client.get(f"{_PRM_BASE}/server/{USER_ID}/github")
    assert resp.status_code == 200
    body = resp.json()
    expected_issuer = downstream_mcp_issuer(settings.jwt_issuer, USER_ID, "github")
    assert body["authorization_servers"] == [expected_issuer]


def test_prm_non_direct_connect_unchanged(test_client: TestClient):
    # mcpgw/mcp (and any non server/ path) always points to the standard registry issuer.
    resp = test_client.get(f"{_PRM_BASE}/mcpgw/mcp")
    assert resp.status_code == 200
    assert resp.json()["authorization_servers"] == [settings.jwt_issuer]


def test_prm_direct_connect_no_oauth_points_to_root_issuer(test_client: TestClient, mock_server_service):
    # Servers with requiresOAuth=False (including AgentCore Runtime MCPs) advertise the root issuer.
    resp = test_client.get(f"{_PRM_BASE}/server/{USER_ID}/agentcore/mcp/myserver")
    assert resp.status_code == 200
    assert resp.json()["authorization_servers"] == [settings.jwt_issuer]


def test_downstream_as_metadata_issuer_and_endpoints(test_client: TestClient):
    resp = test_client.get(f"/.well-known/oauth-authorization-server/proxy/server/oauth/{USER_ID}/github")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == downstream_mcp_issuer(settings.jwt_issuer, USER_ID, "github")
    assert body["authorization_endpoint"].endswith(f"/downstream/oauth/authorize/{USER_ID}/github")
    assert body["token_endpoint"].endswith(f"/downstream/oauth/token/{USER_ID}/github")
    assert body["jwks_uri"] == f"{settings.jwt_issuer}/.well-known/jwks.json"
    assert body["code_challenge_methods_supported"] == ["S256"]
