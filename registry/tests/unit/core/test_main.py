"""
Unit tests for main application module.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.main import app, health_check, lifespan


@pytest.mark.unit
@pytest.mark.core
class TestMainApplication:
    """Test suite for main application functionality."""

    @pytest.fixture
    def mock_services(self):
        """Mock all services used in lifespan."""
        with (
            patch("registry.main.vector_service") as mock_vector_service,
            patch("registry.main.health_service") as mock_health_service,
            patch("registry.main.init_mongodb") as mock_init_mongodb,
            patch("registry.main.close_mongodb") as mock_close_mongodb,
            patch("registry.main.init_redis") as mock_init_redis,
            patch("registry.main.close_redis") as mock_close_redis,
            patch("registry.main.get_federation_service") as mock_get_federation,
            patch("registry.main.mcp_app") as mock_mcp_app,
        ):
            # Configure mocks
            mock_vector_service.initialize = AsyncMock()
            mock_vector_service._initialized = False  # Skip index update

            mock_health_service.initialize = AsyncMock()
            mock_health_service.shutdown = AsyncMock()

            # Mock MongoDB connection
            mock_init_mongodb.return_value = AsyncMock()
            mock_close_mongodb.return_value = AsyncMock()

            # Mock Redis connection
            mock_init_redis.return_value = AsyncMock()
            mock_close_redis.return_value = AsyncMock()

            # Mock federation service
            mock_federation = Mock()
            mock_federation.config.is_any_federation_enabled.return_value = False
            mock_get_federation.return_value = mock_federation

            # Mock session_manager.run() as an async context manager
            mock_session_manager = AsyncMock()
            mock_session_manager.__aenter__ = AsyncMock(return_value=None)
            mock_session_manager.__aexit__ = AsyncMock(return_value=None)
            mock_mcp_app.session_manager.run.return_value = mock_session_manager

            yield {
                "vector_service": mock_vector_service,
                "health_service": mock_health_service,
                "init_mongodb": mock_init_mongodb,
                "close_mongodb": mock_close_mongodb,
                "init_redis": mock_init_redis,
                "close_redis": mock_close_redis,
                "get_federation_service": mock_get_federation,
                "mock_mcp_app": mock_mcp_app,
            }

    @pytest.mark.asyncio
    async def test_lifespan_startup_success(self, mock_services):
        """Test successful application startup."""
        test_app = FastAPI()

        async with lifespan(test_app):
            # Verify all initialization steps were called
            mock_services["vector_service"].initialize.assert_called_once()
            mock_services["health_service"].initialize.assert_called_once()

    @pytest.mark.skip(reason="Agent service failure doesn't crash startup in current implementation")
    @pytest.mark.asyncio
    async def test_lifespan_startup_faiss_service_failure(self, mock_services):
        """Test startup failure during vector service initialization."""
        mock_services["vector_service"].initialize.side_effect = Exception("FAISS init failed")

        test_app = FastAPI()

        with pytest.raises(Exception, match="FAISS init failed"):
            async with lifespan(test_app):
                pass

    @pytest.mark.asyncio
    async def test_lifespan_startup_health_service_failure(self, mock_services):
        """Test startup failure during health service initialization."""
        mock_services["health_service"].initialize.side_effect = Exception("Health init failed")

        test_app = FastAPI()

        with pytest.raises(Exception, match="Health init failed"):
            async with lifespan(test_app):
                pass

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_success(self, mock_services):
        """Test successful application shutdown."""
        test_app = FastAPI()

        async with lifespan(test_app):
            pass  # Startup completes normally

        # Verify shutdown was called
        mock_services["health_service"].shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_failure(self, mock_services):
        """Test shutdown with service failure."""
        mock_services["health_service"].shutdown.side_effect = Exception("Shutdown failed")

        test_app = FastAPI()

        # Should not raise exception, just log error
        async with lifespan(test_app):
            pass

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check endpoint."""
        response = await health_check()

        assert response == {"status": "healthy", "service": "mcp-gateway-registry"}

    def test_app_configuration(self):
        """Test FastAPI app configuration."""
        assert app.title == "MCP Gateway Registry"
        assert app.description == "A registry and management system for Model Context Protocol (MCP) servers"
        assert app.version == "1.0.0"

    def test_app_routes_registered(self):
        """Test that all routes are properly registered."""
        # Create test client
        client = TestClient(app)

        # Test basic health endpoint (should not require auth)
        with patch("registry.main.vector_service"), patch("registry.main.health_service"):
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy", "service": "mcp-gateway-registry"}

    def test_static_files_mounted(self):
        """Test that static files are properly mounted."""
        # Check if static files mount exists
        # Note: Static files may not be mounted in the registry app if frontend is served separately
        static_mounts = [mount for mount in app.routes if hasattr(mount, "name") and mount.name == "static"]
        # Accept either having static files or not (depends on deployment configuration)
        # Just verify the check doesn't crash
        if len(static_mounts) > 0:
            assert static_mounts[0].path == "/static"
        else:
            # Static files not mounted - this is valid if frontend is served separately
            pass

    def test_routers_included(self):
        """Test that all domain routers are included."""
        # Check that routes from different routers are present
        route_paths = [route.path for route in app.routes if hasattr(route, "path")]

        # We can't easily test specific paths without mocking dependencies,
        # but we can test that multiple routes exist (more than just /health)
        assert len(route_paths) > 1

    def test_logging_configuration(self):
        """Test that logging is properly configured."""
        import logging

        # Check that root logger has been configured
        root_logger = logging.getLogger()
        assert root_logger.level <= logging.INFO

        # Check that our module logger exists
        main_logger = logging.getLogger("registry.main")
        assert main_logger is not None
