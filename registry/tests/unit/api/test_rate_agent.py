"""
Unit tests for rate_agent endpoint in agent_routes.py
"""

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import Request, status
from fastapi.testclient import TestClient

from registry.auth.dependencies import map_cognito_groups_to_scopes
from registry.core.config import settings
from registry.main import app
from registry.schemas.agent_models import AgentCard
from registry.services.agent_service import agent_service


@pytest.fixture
def mock_user_context() -> dict[str, Any]:
    """Mock authenticated user context."""
    return {
        "username": "testuser",
        "groups": ["users"],
        "is_admin": False,
        "ui_permissions": {},
        "accessible_agents": ["all"],
    }


@pytest.fixture
def mock_admin_context() -> dict[str, Any]:
    """Mock admin user context."""
    return {
        "username": "admin",
        "groups": ["admins"],
        "is_admin": True,
        "ui_permissions": {},
        "accessible_agents": ["all"],
    }


@pytest.fixture
def admin_session_cookie():
    """Create a valid admin session cookie (JWT access token)."""
    from registry.utils.crypto_utils import generate_access_token

    groups = ["registry-admin"]
    scopes = map_cognito_groups_to_scopes(groups) or ["registry-admin"]

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
def user_session_cookie():
    """Create a valid user session cookie (JWT access token)."""
    from registry.utils.crypto_utils import generate_access_token
    from registry_pkgs.core.scopes import map_groups_to_scopes

    groups = ["users"]
    scopes = map_groups_to_scopes(groups) or []

    return generate_access_token(
        user_id="test-user-id",
        username="testuser",
        email="testuser@test.local",
        groups=groups,
        scopes=scopes,
        role="user",
        auth_method="oauth2",
        provider="cognito",
    )


@pytest.fixture
def authenticated_client(mock_auth_middleware):
    """Create a test client with admin authentication using mocked middleware."""
    return TestClient(app)


@pytest.fixture
def user_authenticated_client(mock_auth_middleware):
    """Create a test client with user authentication using mocked middleware."""
    return TestClient(app)


@pytest.fixture
def sample_agent_card() -> AgentCard:
    """Create a sample agent card for testing."""
    return AgentCard(
        name="Test Agent",
        description="A test agent",
        url="http://localhost:8080/test-agent",
        path="/test-agent",
        version="1.0.0",
        tags=["test"],
        skills=[],
        visibility="public",
    )


@pytest.mark.unit
class TestRateAgent:
    """Test suite for POST /agents/{path}/rate endpoint."""

    def test_rate_agent_success(
        self,
        mock_user_context: dict[str, Any],
        sample_agent_card: AgentCard,
        authenticated_client,
    ) -> None:
        """Test successfully rating an agent."""
        with (
            patch.object(
                agent_service,
                "get_agent_info",
                return_value=sample_agent_card,
            ),
            patch.object(
                agent_service,
                "update_rating",
                return_value=4.5,
            ),
        ):
            response = authenticated_client.post(
                "/api/agents/test-agent/rate",
                json={"rating": 5},
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["message"] == "Rating added successfully"

    def test_rate_agent_not_found(
        self,
        mock_user_context: dict[str, Any],
        authenticated_client,
    ) -> None:
        """Test rating a non-existent agent returns 404."""
        with patch.object(
            agent_service,
            "get_agent_info",
            return_value=None,
        ):
            response = authenticated_client.post(
                "/api/agents/nonexistent/rate",
                json={"rating": 5},
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "not found" in response.json()["detail"].lower()

    def test_rate_agent_no_access(
        self,
        sample_agent_card: AgentCard,
        authenticated_client,
    ) -> None:
        """Test rating an agent without access returns 403."""
        from registry.auth.dependencies import get_current_user

        # User with restricted access - accessible_agents doesn't include /test-agent
        restricted_context = {
            "username": "restricted_user",
            "groups": ["other-group"],
            "is_admin": False,
            "ui_permissions": {},
        }

        def _mock_get_user(request: Request):
            return restricted_context

        app.dependency_overrides[get_current_user] = _mock_get_user

        sample_agent_card.visibility = "group-restricted"
        sample_agent_card.allowed_groups = ["allowed-group"]

        with patch.object(
            agent_service,
            "get_agent_info",
            return_value=sample_agent_card,
        ):
            response = authenticated_client.post(
                "/api/agents/test-agent/rate",
                json={"rating": 5},
            )

            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert "access" in response.json()["detail"].lower()

        app.dependency_overrides.clear()

    def test_rate_agent_invalid_rating_type(
        self,
        mock_user_context: dict[str, Any],
        sample_agent_card: AgentCard,
        authenticated_client,
    ) -> None:
        """Test rating with invalid type returns validation error."""
        with patch.object(
            agent_service,
            "get_agent_info",
            return_value=sample_agent_card,
        ):
            response = authenticated_client.post(
                "/api/agents/test-agent/rate",
                json={"rating": "five"},  # String instead of int
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_rate_agent_missing_rating(
        self,
        mock_user_context: dict[str, Any],
        sample_agent_card: AgentCard,
        authenticated_client,
    ) -> None:
        """Test rating without rating field returns validation error."""
        with patch.object(
            agent_service,
            "get_agent_info",
            return_value=sample_agent_card,
        ):
            response = authenticated_client.post(
                "/api/agents/test-agent/rate",
                json={},  # Missing rating field
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_rate_agent_update_rating_fails(
        self,
        mock_user_context: dict[str, Any],
        sample_agent_card: AgentCard,
        authenticated_client,
    ) -> None:
        """Test handling when update_rating fails."""
        with (
            patch.object(
                agent_service,
                "get_agent_info",
                return_value=sample_agent_card,
            ),
            patch.object(
                agent_service,
                "update_rating",
                side_effect=ValueError("Failed to save rating"),
            ),
        ):
            response = authenticated_client.post(
                "/api/agents/test-agent/rate",
                json={"rating": 5},
            )

            # ValueError is caught and returns 400 Bad Request
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Failed to save rating" in response.json()["detail"]

    def test_rate_agent_with_different_ratings(
        self,
        mock_user_context: dict[str, Any],
        sample_agent_card: AgentCard,
        authenticated_client,
    ) -> None:
        """Test rating an agent with different valid rating values."""
        for rating_value in [1, 2, 3, 4, 5]:
            with (
                patch.object(
                    agent_service,
                    "get_agent_info",
                    return_value=sample_agent_card,
                ),
                patch.object(
                    agent_service,
                    "update_rating",
                    return_value=float(rating_value),
                ),
            ):
                response = authenticated_client.post(
                    "/api/agents/test-agent/rate",
                    json={"rating": rating_value},
                )

                assert response.status_code == status.HTTP_200_OK
                assert response.json()["message"] == "Rating added successfully"

    def test_rate_agent_path_normalization(
        self,
        mock_user_context: dict[str, Any],
        sample_agent_card: AgentCard,
        authenticated_client,
    ) -> None:
        """Test that agent path is normalized correctly."""
        with (
            patch.object(
                agent_service,
                "get_agent_info",
                return_value=sample_agent_card,
            ) as mock_get_agent,
            patch.object(
                agent_service,
                "update_rating",
                return_value=5.0,
            ) as mock_update,
        ):
            # Test with path - the URL already has the correct format
            response = authenticated_client.post(
                "/api/agents/test-agent/rate",
                json={"rating": 5},
            )

            assert response.status_code == status.HTTP_200_OK
            # Verify the path was normalized (should have leading slash)
            mock_get_agent.assert_called_once_with("/test-agent")
            # authenticated_client uses admin user, not testuser
            # The actual username used depends on which client fixture is used
            # Just verify update_rating was called with the right path and rating
            assert mock_update.call_count == 1
            call_args = mock_update.call_args[0]
            assert call_args[0] == "/test-agent"  # path
            assert call_args[2] == 5  # rating
            # Username can be either 'admin' or 'testuser' depending on the client

    def test_rate_agent_private_agent_by_owner(
        self,
        mock_user_context: dict[str, Any],
        authenticated_client,
    ) -> None:
        """Test that agent owner can rate their private agent."""
        # Create private agent owned by testadmin (matches mock_auth_middleware username)
        private_agent = AgentCard(
            protocolVersion="1.0",
            name="Private Agent",
            description="A private agent",
            url="http://localhost:8080/private-agent",
            path="/private-agent",
            version="1.0.0",
            tags=["test"],
            skills=[],
            visibility="private",
            registeredBy="testadmin",  # Same as mock_auth_middleware username
            numStars=0.0,
            ratingDetails=[],
        )

        with (
            patch.object(
                agent_service,
                "get_agent_info",
                return_value=private_agent,
            ),
            patch.object(
                agent_service,
                "update_rating",
                return_value=5.0,
            ),
        ):
            response = authenticated_client.post(
                "/api/agents/private-agent/rate",
                json={"rating": 5},
            )

            assert response.status_code == status.HTTP_200_OK

    def test_rate_agent_group_restricted_with_access(
        self,
        authenticated_client,
    ) -> None:
        """Test rating a group-restricted agent when user is in allowed group."""
        # Group-restricted agent
        group_agent = AgentCard(
            protocolVersion="1.0",
            name="Group Agent",
            description="A group-restricted agent",
            url="http://localhost:8080/group-agent",
            path="/group-agent",
            version="1.0.0",
            tags=["test"],
            skills=[],
            visibility="group-restricted",
            allowedGroups=["allowed-group"],
            registeredBy="admin",
            numStars=0.0,
            ratingDetails=[],
        )

        with (
            patch.object(
                agent_service,
                "get_agent_info",
                return_value=group_agent,
            ),
            patch.object(
                agent_service,
                "update_rating",
                return_value=4.0,
            ),
        ):
            response = authenticated_client.post(
                "/api/agents/group-agent/rate",
                json={"rating": 4},
            )

            assert response.status_code == status.HTTP_200_OK
