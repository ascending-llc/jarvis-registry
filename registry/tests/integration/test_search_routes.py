"""Integration tests for the unified semantic search route."""

from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from registry.deps import get_container
from registry.main import app
from tests.conftest import make_container, make_container_factory


@pytest.mark.integration
@pytest.mark.search
class TestSearchRoutes:
    """Integration coverage for POST /api/v1/search."""

    def setup_method(self):
        """Override auth dependency for integration testing."""
        from registry.auth.dependencies import get_current_user

        user_context = {
            "username": "test-admin",
            "user_id": "test-admin-id",
            "is_admin": True,
            "accessible_servers": ["all"],
            "accessible_agents": ["all"],
            "accessible_services": ["all"],
            "groups": ["registry-admin"],
            "scopes": ["registry-admin"],
            "ui_permissions": {},
            "can_modify_servers": True,
            "auth_method": "traditional",
            "provider": "local",
        }

        def _mock_auth(request: Request):
            return user_context

        app.dependency_overrides[get_current_user] = _mock_auth
        app.state.container = make_container(
            server_service=AsyncMock(),
            search_service=AsyncMock(),
            status_resolver=AsyncMock(),
        )

    def teardown_method(self):
        """Clean up dependency overrides."""
        app.dependency_overrides.clear()

    def test_search_success_returns_mcp_and_a2a_results(self, test_client: TestClient):
        """A successful search maps MCP servers/tools and A2A agents/skills."""
        mock_results = {
            "servers": [
                {
                    "path": "/demo",
                    "server_name": "Demo",
                    "description": "Demo server",
                    "tags": ["demo"],
                    "num_tools": 1,
                    "is_enabled": True,
                    "relevance_score": 0.9,
                    "match_context": "Demo server",
                    "matching_tools": [
                        {
                            "tool_name": "alpha",
                            "description": "Alpha tool",
                            "relevance_score": 0.8,
                            "match_context": "Alpha tool",
                        }
                    ],
                }
            ],
            "tools": [
                {
                    "server_path": "/demo",
                    "server_name": "Demo",
                    "tool_name": "alpha",
                    "description": "Alpha tool",
                    "match_context": "Alpha tool",
                    "relevance_score": 0.85,
                }
            ],
            "agents": [
                {
                    "agent_id": "agent-1",
                    "path": "/agent/demo",
                    "agent_name": "Demo Agent",
                    "description": "Helps with demos",
                    "tags": ["demo"],
                    "enabled": True,
                    "relevance_score": 0.77,
                    "match_context": "Helps with demos",
                }
            ],
            "skills": [
                {
                    "agent_id": "agent-1",
                    "path": "/agent/demo",
                    "agent_name": "Demo Agent",
                    "skill_name": "explain",
                    "description": "Explains things",
                    "relevance_score": 0.7,
                    "match_context": "Explains things",
                }
            ],
        }

        search_service = AsyncMock()
        search_service.semantic_search = AsyncMock(return_value=mock_results)
        app.dependency_overrides[get_container] = make_container_factory(search_service=search_service)

        response = test_client.post(
            "/api/v1/search",
            json={
                "query": "alpha",
                "entityTypes": ["mcp_server", "tool", "a2a_agent", "skill"],
                "maxResults": 5,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["totalServers"] == 1
        assert data["totalTools"] == 1
        assert data["servers"][0]["serverName"] == "Demo"
        assert data["tools"][0]["toolName"] == "alpha"
        assert data["totalAgents"] == 1
        assert data["agents"][0]["agentName"] == "Demo Agent"
        assert data["totalSkills"] == 1
        assert data["skills"][0]["skillName"] == "explain"

    def test_search_handles_service_errors(self, test_client: TestClient):
        """Service-level errors propagate as 503."""
        search_service = AsyncMock()
        search_service.semantic_search = AsyncMock(side_effect=RuntimeError("offline"))
        app.dependency_overrides[get_container] = make_container_factory(search_service=search_service)

        response = test_client.post("/api/v1/search", json={"query": "alpha"})

        assert response.status_code == 503
        assert "temporarily unavailable" in response.json()["detail"]

    def test_deleted_routes_return_404(self, test_client: TestClient):
        """The removed /search/servers and /search/agents routes no longer exist."""
        servers_response = test_client.post("/api/v1/search/servers", json={"query": "x", "top_n": 5})
        agents_response = test_client.post("/api/v1/search/agents", json={"query": "x"})

        assert servers_response.status_code == 404
        assert agents_response.status_code == 404
