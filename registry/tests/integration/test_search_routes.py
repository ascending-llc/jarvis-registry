"""
Integration tests for semantic search routes.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from registry.main import app


@pytest.mark.integration
@pytest.mark.search
class TestSearchRoutes:
    """Integration coverage for /api/search/semantic."""

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

    def teardown_method(self):
        """Clean up dependency overrides."""
        app.dependency_overrides.clear()

    def test_semantic_search_success(self, test_client: TestClient):
        """Successful semantic search returns filtered data."""
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
                    "path": "/agent/demo",
                    "agent_name": "Demo Agent",
                    "description": "Helps with demos",
                    "tags": ["demo"],
                    "skills": ["explain"],
                    "visibility": "public",
                    "trust_level": "verified",
                    "is_enabled": True,
                    "relevance_score": 0.77,
                    "match_context": "Helps with demos",
                }
            ],
        }

        with (
            patch("registry.api.v1.search_routes.faiss_service") as mock_faiss,
            patch("registry.api.v1.search_routes.agent_service") as mock_agent_service,
        ):
            mock_faiss.search_mixed = AsyncMock(return_value=mock_results)
            mock_agent = Mock()
            mock_agent.visibility = "public"
            mock_agent.model_dump.return_value = {
                "name": "Demo Agent",
                "description": "Helps with demos",
                "tags": ["demo"],
                "skills": [{"name": "explain"}],
                "visibility": "public",
                "trust_level": "verified",
                "is_enabled": True,
            }
            mock_agent_service.get_agent_info.return_value = mock_agent

            response = test_client.post(
                "/api/v1/search/semantic",
                json={
                    "query": "alpha",
                    "entity_types": ["mcp_server", "tool", "a2a_agent"],
                    "max_results": 5,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_servers"] == 1
        assert data["total_tools"] == 1
        assert data["total_agents"] == 1
        assert data["servers"][0]["server_name"] == "Demo"
        assert data["tools"][0]["tool_name"] == "alpha"
        assert data["agents"][0]["agent_name"] == "Demo Agent"

    def test_semantic_search_handles_service_errors(self, test_client: TestClient):
        """Service-level errors propagate as 503."""
        with patch("registry.api.v1.search_routes.faiss_service") as mock_faiss:
            mock_faiss.search_mixed = AsyncMock(side_effect=RuntimeError("offline"))

            response = test_client.post("/api/v1/search/semantic", json={"query": "alpha"})

        assert response.status_code == 503
        assert "temporarily unavailable" in response.json()["detail"]


@pytest.mark.integration
@pytest.mark.search
class TestServerSearchRoutes:
    """Integration coverage for /api/v1/search endpoint."""

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

    def teardown_method(self):
        """Clean up dependency overrides."""
        app.dependency_overrides.clear()

    def test_search_servers_success_with_hybrid_search(self, test_client: TestClient):
        """Successful server search with hybrid search type."""
        mock_search_results = [{"server_id": "test-server-1", "relevance_score": 0.95}]

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post(
                "/api/v1/search/servers", json={"query": "test query", "top_n": 5, "search_type": "hybrid"}
            )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "query" in data
        assert "total" in data
        assert "servers" in data
        assert data["query"] == "test query"

        # Verify search was called with correct parameters
        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["query"] == "test query"
        assert call_kwargs["k"] == 5

    def test_search_servers_with_near_text_search(self, test_client: TestClient):
        """Server search supports near_text search type."""
        from registry_pkgs.vector.enum.enums import SearchType

        mock_search_results = []

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post(
                "/api/v1/search/servers", json={"query": "semantic query", "top_n": 3, "search_type": "near_text"}
            )

        assert response.status_code == 200

        # Verify SearchType.NEAR_TEXT was used
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["search_type"] == SearchType.NEAR_TEXT

    def test_search_servers_with_bm25_search(self, test_client: TestClient):
        """Server search supports bm25 search type."""
        from registry_pkgs.vector.enum.enums import SearchType

        mock_search_results = []

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post(
                "/api/v1/search/servers", json={"query": "keyword query", "top_n": 10, "search_type": "bm25"}
            )

        assert response.status_code == 200

        # Verify SearchType.BM25 was used
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["search_type"] == SearchType.BM25

    def test_search_servers_with_similarity_store_search(self, test_client: TestClient):
        """Server search supports similarity_store search type."""
        from registry_pkgs.vector.enum.enums import SearchType

        mock_search_results = []

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post(
                "/api/v1/search/servers",
                json={
                    "query": "test",
                    "top_n": 5,
                    "search_type": "SIMILARITY_STORE",  # Use uppercase to match enum value
                },
            )

        assert response.status_code == 200

        # Verify SearchType.SIMILARITY_STORE was used
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["search_type"] == SearchType.SIMILARITY_STORE

    def test_search_servers_defaults_to_hybrid(self, test_client: TestClient):
        """Server search defaults to hybrid when search_type not specified."""
        from registry_pkgs.vector.enum.enums import SearchType

        mock_search_results = []

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post("/api/v1/search/servers", json={"query": "test", "top_n": 5})

        assert response.status_code == 200

        # Verify default search type is HYBRID
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["search_type"] == SearchType.HYBRID

    def test_search_servers_handles_invalid_search_type(self, test_client: TestClient):
        """Server search rejects invalid search_type with validation error."""
        response = test_client.post(
            "/api/v1/search/servers", json={"query": "test", "top_n": 5, "search_type": "invalid_type"}
        )

        # Pydantic validates enum values and returns 422 for invalid values
        assert response.status_code == 422
        assert "search_type" in response.json()["detail"]

    def test_search_servers_respects_top_n(self, test_client: TestClient):
        """Server search respects the top_n parameter."""
        mock_search_results = []

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post("/api/v1/search/servers", json={"query": "test", "top_n": 3})

        assert response.status_code == 200

        # Verify k parameter matches top_n
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["k"] == 3

    def test_search_servers_uses_reranking(self, test_client: TestClient):
        """Server search uses reranking with candidate_k."""
        mock_search_results = []

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post("/api/v1/search/servers", json={"query": "test", "top_n": 5})

        assert response.status_code == 200

        # Verify candidate_k is 5x top_n (capped at 100)
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["candidate_k"] == 25  # 5 * 5

    def test_search_servers_caps_candidate_k_at_100(self, test_client: TestClient):
        """Server search caps candidate_k at 100."""
        mock_search_results = []

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post(
                "/api/v1/search/servers",
                json={
                    "query": "test",
                    "top_n": 50,  # Would be 250 without cap
                },
            )

        assert response.status_code == 200

        # Verify candidate_k is capped at 100
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["candidate_k"] == 100

    def test_search_servers_empty_query(self, test_client: TestClient):
        """Server search rejects empty query string."""
        response = test_client.post("/api/v1/search/servers", json={"query": "", "top_n": 10})

        # Query has min_length=1, so empty string returns validation error
        assert response.status_code == 422
        assert "query" in response.json()["detail"]

    def test_search_servers_returns_results(self, test_client: TestClient):
        """Server search returns search results."""
        mock_search_results = [{"server_id": "test-id-1", "server_name": "Test Server", "relevance_score": 0.9}]

        with patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_search_results

            response = test_client.post("/api/v1/search/servers", json={"query": "test", "top_n": 5})

        assert response.status_code == 200

        # Verify search was called
        mock_search.assert_called_once()

        # Verify response structure
        data = response.json()
        assert "query" in data
        assert "total" in data
        assert "servers" in data
        assert data["query"] == "test"
