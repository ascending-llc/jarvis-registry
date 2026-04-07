"""Registry entrypoint and application lifecycle wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI

from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.database.redis_client import close_redis_client, create_redis_client
from registry_pkgs.telemetry import setup_metrics
from registry_pkgs.vector.client import create_database_client

from .app_factory import create_app
from .container import RegistryContainer
from .core.config import settings
from .mcpgw import create_gateway_mcp_app

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from .mcpgw.core.types import McpAppContext

logger = logging.getLogger(__name__)

app: FastAPI

gateway_mcp_app: FastMCP[McpAppContext]


def _get_current_container() -> RegistryContainer | None:
    """Return the app-scoped dependency container for route dependencies and MCP tools."""
    return getattr(app.state, "container", None)


def _get_gateway_mcp_app(app: FastAPI):
    """Resolve the mounted MCP gateway from app state, with a module-level fallback for tests."""
    return getattr(app.state, "gateway_mcp_app", gateway_mcp_app)


class _RuntimeResources:
    """Keep track of infrastructure clients that must be closed during shutdown."""

    def __init__(self):
        self.db_client = None
        self.redis_client = None


def _initialize_telemetry() -> None:
    """Best-effort telemetry setup that should not block the application from starting."""
    logger.info("Initializing telemetry")
    try:
        setup_metrics("mcp-gateway-registry", settings.telemetry_config)
    except Exception as exc:
        logger.warning("Failed to initialize telemetry: %s", exc)


async def _startup_container(app: FastAPI, resources: _RuntimeResources) -> RegistryContainer:
    """Create infra clients, build the registry container, and expose it on app.state."""
    logger.info("Initializing MongoDB connection")
    await init_mongodb(settings.mongo_config)

    logger.info("Initializing Redis connection")
    resources.redis_client = create_redis_client(settings.redis_config)

    logger.info("Initializing vector database client")
    resources.db_client = create_database_client(settings.vector_backend_config)

    container = RegistryContainer(
        settings=settings,
        db_client=resources.db_client,
        redis_client=resources.redis_client,
    )
    app.state.container = container
    await container.startup()
    return container


async def _shutdown_container(app: FastAPI, resources: _RuntimeResources) -> None:
    """Shutdown app-scoped services before tearing down the underlying infra clients."""
    container = getattr(app.state, "container", None)
    if container is not None:
        try:
            await container.shutdown()
        except Exception as exc:
            logger.error("Container shutdown error: %s", exc, exc_info=True)
        finally:
            del app.state.container

    try:
        logger.info("Closing Redis connection")
        close_redis_client(resources.redis_client)
    except Exception as exc:
        logger.error("Redis close error: %s", exc, exc_info=True)

    if resources.db_client is not None:
        try:
            logger.info("Closing vector database client")
            resources.db_client.close()
        except Exception as exc:
            logger.error("Vector database client close error: %s", exc, exc_info=True)

    try:
        logger.info("Closing MongoDB connection")
        await close_mongodb()
    except Exception as exc:
        logger.error("MongoDB close error: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the full application lifecycle around one app-scoped dependency container."""
    settings.configure_logging()
    logger.info("Starting MCP Gateway Registry")
    resources = _RuntimeResources()

    try:
        _initialize_telemetry()
        await _startup_container(app, resources)
        logger.info("Application startup completed")
    except Exception as exc:
        logger.error("Failed to initialize services: %s", exc, exc_info=True)
        raise

    async with _get_gateway_mcp_app(app).session_manager.run():
        yield

    logger.info("Shutting down MCP Gateway Registry")
    try:
        await _shutdown_container(app, resources)
        logger.info("Application shutdown completed")
    except Exception as exc:
        logger.error("Error during shutdown: %s", exc, exc_info=True)


# The gateway is created once here, but it resolves the active container lazily
# through ``_get_current_container`` so each request uses the current app state.
gateway_mcp_app = create_gateway_mcp_app(container_provider=_get_current_container)

# The FastAPI app is exposed at module level so ASGI servers can import ``app``
# directly while still keeping the startup and shutdown wiring in ``lifespan``.
app = create_app(lifespan=lifespan, gateway_mcp_app=gateway_mcp_app)


if __name__ == "__main__":
    uvicorn.run(
        "registry.main:app",
        host="0.0.0.0",  # nosec B104 - it's fine to bind to 0.0.0.0 in a container.
        port=7860,
        reload=True,
        log_level=settings.log_level.lower(),
        log_config=None,  # Disable uvicorn's default logging config to use ours
    )
