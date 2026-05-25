"""Unit tests for the registry entrypoint module."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.core.config import settings
from registry.main import app, lifespan


def _mock_async_context_manager() -> AsyncMock:
    manager = AsyncMock()
    manager.__aenter__ = AsyncMock(return_value=None)
    manager.__aexit__ = AsyncMock(return_value=None)
    return manager


@pytest.mark.unit
@pytest.mark.core
class TestMainApplication:
    """Test suite for main application functionality."""

    @pytest.fixture(autouse=True)
    def _reset_agno_cancellation_manager(self):
        """Restore agno's global cancellation manager after each test.

        ``lifespan`` installs a process-global MongoBackedCancellationManager
        during startup. A startup that fails *after* that install (e.g.
        ``test_lifespan_startup_container_failure``) never reaches shutdown, so
        the global leaks and the next lifespan startup trips the install guard.
        """
        yield
        from agno.run.cancel import set_cancellation_manager
        from agno.run.cancellation_management.in_memory_cancellation_manager import (
            InMemoryRunCancellationManager,
        )

        set_cancellation_manager(InMemoryRunCancellationManager())

    @pytest.fixture
    def mock_services(self):
        """Mock the startup/shutdown dependencies used by lifespan."""
        mock_container = Mock()
        mock_container.startup = AsyncMock()
        mock_container.shutdown = AsyncMock()

        with (
            patch("registry.main.RegistryContainer", return_value=mock_container) as mock_container_cls,
            patch("registry.main.init_mongodb", new=AsyncMock()) as mock_init_mongodb,
            patch("registry.main.close_mongodb", new=AsyncMock()) as mock_close_mongodb,
            patch("registry.main.create_redis_client") as mock_create_redis_client,
            patch("registry.main.close_redis_client") as mock_close_redis_client,
            patch("registry.main.create_database_client") as mock_create_database_client,
        ):
            mock_create_redis_client.return_value = Mock()
            mock_create_database_client.return_value = Mock()
            mock_gateway_mcp_app = Mock()
            mock_gateway_mcp_app.session_manager.run.return_value = _mock_async_context_manager()

            yield {
                "container": mock_container,
                "container_cls": mock_container_cls,
                "init_mongodb": mock_init_mongodb,
                "close_mongodb": mock_close_mongodb,
                "create_redis_client": mock_create_redis_client,
                "close_redis_client": mock_close_redis_client,
                "create_database_client": mock_create_database_client,
                "gateway_mcp_app": mock_gateway_mcp_app,
            }

    @pytest.mark.asyncio
    async def test_lifespan_startup_success(self, mock_services):
        """Test successful application startup."""
        test_app = FastAPI()
        test_app.state.gateway_mcp_app = mock_services["gateway_mcp_app"]

        async with lifespan(test_app):
            assert test_app.state.container is mock_services["container"]

        mock_services["init_mongodb"].assert_awaited_once_with(settings.mongo_config)
        mock_services["create_redis_client"].assert_called_once_with(settings.redis_config)
        mock_services["create_database_client"].assert_called_once_with(settings.vector_backend_config)
        mock_services["container_cls"].assert_called_once_with(
            settings=settings,
            db_client=mock_services["create_database_client"].return_value,
            redis_client=mock_services["create_redis_client"].return_value,
        )
        mock_services["container"].startup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_startup_container_failure(self, mock_services):
        """Container startup failures should bubble up."""
        mock_services["container"].startup.side_effect = Exception("container startup failed")
        test_app = FastAPI()
        test_app.state.gateway_mcp_app = mock_services["gateway_mcp_app"]

        with pytest.raises(Exception, match="container startup failed"):
            async with lifespan(test_app):
                pass

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_success(self, mock_services):
        """Test successful application shutdown."""
        test_app = FastAPI()
        test_app.state.gateway_mcp_app = mock_services["gateway_mcp_app"]

        async with lifespan(test_app):
            pass

        mock_services["container"].shutdown.assert_awaited_once()
        mock_services["close_redis_client"].assert_called_once()
        mock_services["create_database_client"].return_value.close.assert_called_once()
        mock_services["close_mongodb"].assert_awaited_once()
        assert not hasattr(test_app.state, "container")

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_failure(self, mock_services):
        """Shutdown failures are swallowed and logged by lifespan."""
        mock_services["container"].shutdown.side_effect = Exception("shutdown failed")
        test_app = FastAPI()
        test_app.state.gateway_mcp_app = mock_services["gateway_mcp_app"]

        async with lifespan(test_app):
            pass

        mock_services["close_redis_client"].assert_called_once()
        mock_services["create_database_client"].return_value.close.assert_called_once()
        mock_services["close_mongodb"].assert_awaited_once()

    def test_app_configuration(self):
        """Test FastAPI app configuration."""
        assert app.title == "MCP Gateway Registry"
        assert app.description == "A registry and management system for Model Context Protocol (MCP) servers"
        assert app.version == settings.build_version

    def test_health_route(self):
        """Test the basic health endpoint registered on the app."""
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "mcp-gateway-registry"}

    def test_static_files_mounted(self):
        """Test that static files are properly mounted."""
        static_mounts = [mount for mount in app.routes if hasattr(mount, "name") and mount.name == "static"]
        if static_mounts:
            assert static_mounts[0].path == "/static"

    def test_routers_included(self):
        """Test that multiple routes are registered on the application."""
        route_paths = [route.path for route in app.routes if hasattr(route, "path")]
        assert len(route_paths) > 1

    def test_logging_configuration(self):
        """Test that our module logger exists."""
        import logging

        main_logger = logging.getLogger("registry.main")
        assert main_logger is not None
