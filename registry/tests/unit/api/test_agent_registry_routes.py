"""
Unit tests for Anthropic MCP Registry API endpoints for A2A agents.
"""

from typing import (
    Any,
)
from unittest.mock import (
    patch,
)

import pytest
from fastapi import Request, status
from fastapi.testclient import TestClient

from registry.constants import REGISTRY_CONSTANTS
from registry.core.config import settings
from registry.main import app
from registry.services.agent_service import agent_service


@pytest.fixture
def sample_agent_card() -> dict[str, Any]:
    """Create a sample agent card for testing."""
    return {
        "protocol_version": "1.0",
        "name": "Code Reviewer Agent",
        "description": "Reviews code for quality and best practices",
        "url": "http://localhost:8080/agents/code-reviewer",
        "path": "/agents/code-reviewer",
        "version": "1.0.0",
        "provider": "Test Provider",
        "tags": ["code-review", "testing", "qa"],
        "skills": [
            {
                "id": "review-python",
                "name": "Review Python Code",
                "description": "Reviews Python code",
            }
        ],
        "num_stars": 15,
        "license": "MIT",
        "visibility": "public",
        "trust_level": "community",
        "is_enabled": True,
        "health_status": "healthy",
        "last_checked_iso": None,
    }


@pytest.fixture
def admin_session_cookie():
    """Create a valid admin session cookie (JWT access token)."""
    from registry.utils.crypto_utils import generate_access_token
    from registry_pkgs.core.scopes import map_groups_to_scopes

    groups = ["registry-admins"]
    scopes = map_groups_to_scopes(groups) or ["registry-admins"]

    return generate_access_token(
        user_id="test-admin-id",
        username=settings.admin_user,
        email="admin@test.local",
        groups=groups,
        scopes=scopes,
        role="admin",
        auth_method="traditional",
        provider="local",
    )


@pytest.fixture
def authenticated_client(mock_auth_middleware):
    """Create a test client with admin authentication using mocked middleware."""
    return TestClient(app)


@pytest.fixture
def agents_list(
    sample_agent_card: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create a list of agents for pagination testing."""
    agent1 = sample_agent_card.copy()
    agent1["path"] = "/agents/agent-alpha"
    agent1["name"] = "Agent Alpha"

    agent2 = sample_agent_card.copy()
    agent2["path"] = "/agents/agent-beta"
    agent2["name"] = "Agent Beta"

    agent3 = sample_agent_card.copy()
    agent3["path"] = "/agents/agent-gamma"
    agent3["name"] = "Agent Gamma"

    return [agent1, agent2, agent3]


@pytest.fixture
def user_context() -> dict[str, Any]:
    """Create authenticated user context."""
    return {
        "username": "testuser",
        "groups": ["users"],
        "is_admin": False,
    }


# @pytest.mark.unit
# class TestListAgents:
#     """Test suite for GET /{api_version}/agents endpoint."""
#
#     def test_list_agents_success(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         agents_list: List[Dict[str, Any]],
#         authenticated_client,
#     ) -> None:
#         """Test that list agents returns paginated list with metadata."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "list_agents",
#             return_value=agents_list,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert "servers" in data
#             assert "metadata" in data
#             assert len(data["servers"]) == 3
#             assert data["metadata"]["count"] == 3
#
#         app.dependency_overrides.clear()
#
#     def test_list_agents_with_limit(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         agents_list: List[Dict[str, Any]],
#         authenticated_client,
#     ) -> None:
#         """Test list agents respects limit parameter."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "list_agents",
#             return_value=agents_list,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents?limit=2"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert len(data["servers"]) == 2
#             assert data["metadata"]["count"] == 2
#
#         app.dependency_overrides.clear()
#
#     def test_list_agents_pagination_cursor(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         agents_list: List[Dict[str, Any]],
#         authenticated_client,
#     ) -> None:
#         """Test list agents pagination with cursor parameter."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "list_agents",
#             return_value=agents_list,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents?limit=1"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert len(data["servers"]) == 1
#             assert data["metadata"]["nextCursor"] is not None
#
#         app.dependency_overrides.clear()
#
#     def test_list_agents_max_limit(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         agents_list: List[Dict[str, Any]],
#         authenticated_client,
#     ) -> None:
#         """Test list agents enforces max limit of 1000."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "list_agents",
#             return_value=agents_list,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             # limit=2000 is rejected by validation (max 1000)
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents?limit=2000"
#             )
#
#             # Should return 422 validation error for exceeding max limit
#             assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
#
#         app.dependency_overrides.clear()
#
#     def test_list_agents_empty(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         authenticated_client,
#     ) -> None:
#         """Test list agents returns empty list when no agents."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "list_agents",
#             return_value=[],
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert data["servers"] == []
#             assert data["metadata"]["count"] == 0
#
#         app.dependency_overrides.clear()
#
#     def test_list_agents_only_enabled(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         agents_list: List[Dict[str, Any]],
#         authenticated_client,
#     ) -> None:
#         """Test list agents returns only enabled agents."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         def _is_agent_enabled(path: str) -> bool:
#             """Only enable first agent."""
#             return path == "/agents/agent-alpha"
#
#         with patch.object(
#             agent_service,
#             "list_agents",
#             return_value=agents_list,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             side_effect=_is_agent_enabled,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert len(data["servers"]) == 1
#
#         app.dependency_overrides.clear()
#

# @pytest.mark.unit
# class TestListAgentVersions:
#     """Test suite for GET /{api_version}/agents/{agentName}/versions endpoint."""
#
#     def test_list_versions_success(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test listing versions for an agent."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=sample_agent_card,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert "servers" in data
#             assert len(data["servers"]) == 1
#
#         app.dependency_overrides.clear()
#
#     def test_list_versions_url_decoding(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test listing versions handles URL-encoded names correctly."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=sample_agent_card,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#
#         app.dependency_overrides.clear()
#
#     def test_list_versions_trailing_slash(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test listing versions works with trailing slash handling."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=None,
#         ):
#             # First call returns None, but second call (with slash) succeeds
#             agent_service.get_agent = Mock(
#                 side_effect=[None, sample_agent_card]
#             )
#
#             with patch.object(
#                 agent_service,
#                 "is_agent_enabled",
#                 return_value=True,
#             ):
#                 response = authenticated_client.get(
#                     f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions"
#                 )
#
#                 assert response.status_code == status.HTTP_200_OK
#
#         app.dependency_overrides.clear()
#
#     def test_list_versions_not_found(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         authenticated_client,
#     ) -> None:
#         """Test listing versions for non-existent agent returns 404."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=None,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fnonexistent/versions"
#             )
#
#             assert response.status_code == status.HTTP_404_NOT_FOUND
#
#         app.dependency_overrides.clear()
#
#     def test_list_versions_disabled_agent(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test listing versions for disabled agent returns 404."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=sample_agent_card,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=False,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions"
#             )
#
#             assert response.status_code == status.HTTP_404_NOT_FOUND
#
#         app.dependency_overrides.clear()


# @pytest.mark.unit
# class TestGetAgentVersion:
#     """Test suite for GET /{api_version}/agents/{agentName}/versions/{version} endpoint."""
#
#     def test_get_version_latest(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test getting agent details with 'latest' version."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=sample_agent_card,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions/latest"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert "server" in data
#             assert "_meta" in data
#             assert (
#                 data["server"]["name"] ==
#                 "io.mcpgateway/agents/code-reviewer"
#             )
#
#         app.dependency_overrides.clear()
#
#     def test_get_version_specific(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test getting agent details with specific version."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         # Update agent card to have matching protocol version
#         agent_card_with_version = sample_agent_card.copy()
#         agent_card_with_version["protocol_version"] = "1.0.0"
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=agent_card_with_version,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions/1.0.0"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert data["server"]["version"] == "1.0.0"
#
#         app.dependency_overrides.clear()
#
#     def test_get_version_includes_metadata(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test getting agent version response has all required metadata."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=sample_agent_card,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions/latest"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#             data = response.json()
#
#             assert "server" in data
#             assert "name" in data["server"]
#             assert "description" in data["server"]
#             assert "version" in data["server"]
#             assert "packages" in data["server"]
#             assert "_meta" in data["server"]
#
#         app.dependency_overrides.clear()
#
#     def test_get_version_url_decoding(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test getting agent version handles URL-encoded names."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=sample_agent_card,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions/1.0.0"
#             )
#
#             assert response.status_code == status.HTTP_200_OK
#
#         app.dependency_overrides.clear()
#
#     def test_get_version_not_found(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         authenticated_client,
#     ) -> None:
#         """Test getting version for non-existent agent returns 404."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=None,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fnonexistent/versions/latest"
#             )
#
#             assert response.status_code == status.HTTP_404_NOT_FOUND
#
#         app.dependency_overrides.clear()
#
#     def test_get_version_invalid_version(
#         self,
#         mock_nginx_proxied_auth_admin: Any,
#         sample_agent_card: Dict[str, Any],
#         authenticated_client,
#     ) -> None:
#         """Test getting invalid version returns 404."""
#         from registry.auth.dependencies import nginx_proxied_auth
#
#         app.dependency_overrides[nginx_proxied_auth] = (
#             mock_nginx_proxied_auth_admin
#         )
#
#         with patch.object(
#             agent_service,
#             "get_agent",
#             return_value=sample_agent_card,
#         ), patch.object(
#             agent_service,
#             "is_agent_enabled",
#             return_value=True,
#         ):
#             response = authenticated_client.get(
#                 f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions/2.0.0"
#             )
#
#             assert response.status_code == status.HTTP_404_NOT_FOUND
#
#         app.dependency_overrides.clear()


@pytest.mark.unit
class TestErrorHandling:
    """Test suite for error handling in agent registry routes."""

    def test_error_invalid_agent_name_format(
        self,
        authenticated_client,
    ) -> None:
        """Test invalid agent name format returns 404."""
        response = authenticated_client.get(
            f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/invalid-format/versions"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_error_missing_auth(
        self,
        agents_list: list[dict[str, Any]],
    ) -> None:
        """Test missing auth returns 401 when no credentials provided."""
        # Use TestClient without mock_auth_middleware to test missing auth
        client = TestClient(app)
        response = client.get(f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents")

        # Should fail due to missing auth
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_error_disabled_agent(
        self,
        sample_agent_card: dict[str, Any],
        authenticated_client,
    ) -> None:
        """Test disabled agent returns 404."""
        with (
            patch.object(
                agent_service,
                "get_agent",
                return_value=sample_agent_card,
            ),
            patch.object(
                agent_service,
                "is_agent_enabled",
                return_value=False,
            ),
        ):
            response = authenticated_client.get(
                f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents/io.mcpgateway%2Fagents%2Fcode-reviewer/versions/latest"
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND

    # def test_error_invalid_limit(
    #     self,
    #     mock_nginx_proxied_auth_admin: Any,
    #     authenticated_client,
    # ) -> None:
    #     """Test invalid limit parameter returns validation error."""
    #     from registry.auth.dependencies import nginx_proxied_auth
    #
    #     app.dependency_overrides[nginx_proxied_auth] = (
    #         mock_nginx_proxied_auth_admin
    #     )
    #
    #     with patch.object(
    #         agent_service,
    #         "list_agents",
    #         return_value=[],
    #     ):
    #         response = authenticated_client.get(
    #             f"/{REGISTRY_CONSTANTS.ANTHROPIC_API_VERSION}/agents?limit=0"
    #         )
    #
    #         assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    #
    #     app.dependency_overrides.clear()
