"""Registry entrypoint and application lifecycle wiring."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from agno.run.cancel import get_cancellation_manager, set_cancellation_manager
from agno.run.cancellation_management.in_memory_cancellation_manager import InMemoryRunCancellationManager
from fastapi import FastAPI

from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.database.redis_client import close_redis_client, create_redis_client
from registry_pkgs.telemetry import setup_metrics
from registry_pkgs.vector.client import create_database_client
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.hitl import MongoBackedCancellationManager

from .app_factory import create_app
from .container import RegistryContainer
from .core.config import settings
from .mcpgw import create_gateway_mcp_app

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from redis import Redis

    from registry_pkgs.vector.client import DatabaseClient

    from .mcpgw.core.types import McpAppContext

settings.configure_logging("registry")

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
    db_client: DatabaseClient
    redis_client: Redis

    """Keep track of infrastructure clients that must be closed during shutdown."""

    def __init__(self, db_client: DatabaseClient, redis_client: Redis):
        self.db_client = db_client
        self.redis_client = redis_client


def _initialize_telemetry() -> None:
    """Best-effort telemetry setup that should not block the application from starting."""
    logger.info("Initializing telemetry")
    try:
        setup_metrics("mcp-gateway-registry", settings.telemetry_config)
    except Exception as exc:
        logger.warning("Failed to initialize telemetry: %s", exc)


async def _startup_container(app: FastAPI) -> _RuntimeResources:
    """Create infra clients, build the registry container, and expose it on app.state."""
    logger.info("Initializing MongoDB connection")
    await init_mongodb(settings.mongo_config)

    logger.info("Initializing Redis connection")
    redis_client = create_redis_client(settings.redis_config)

    logger.info("Initializing vector database client")
    db_client = create_database_client(settings.vector_backend_config)

    container = RegistryContainer(
        settings=settings,
        db_client=db_client,
        redis_client=redis_client,
    )
    _install_agno_cancellation_manager(container.directive_queue)
    app.state.container = container
    await container.startup()
    return _RuntimeResources(db_client, redis_client)


def _install_agno_cancellation_manager(directive_queue: DirectiveQueue) -> None:
    """Install our MongoDB-backed cancellation manager process-globally.

    Refuses to overwrite a non-default manager so a second app spinning up in
    the same process (common in tests) does not silently clobber the first.
    """
    current = get_cancellation_manager()
    if not isinstance(current, InMemoryRunCancellationManager):
        raise RuntimeError(
            f"agno cancellation manager already replaced by {type(current).__name__}; "
            "refusing to overwrite (likely a duplicate startup)."
        )
    try:
        set_cancellation_manager(MongoBackedCancellationManager(directive_queue=directive_queue))
        logger.info("installed MongoBackedCancellationManager (agno cancel signal source, queue wired)")
    except Exception as exc:
        logger.error("Failed to install MongoBackedCancellationManager: %s", exc, exc_info=True)
        raise


def _restore_default_cancellation_manager() -> None:
    """Reset agno's global cancellation manager back to the in-memory default.

    Called from ``_shutdown_container`` so a test or sequential ``app`` startup
    does not see our MongoDB-backed manager pointing at a closed Mongo client.
    """
    try:
        set_cancellation_manager(InMemoryRunCancellationManager())
        logger.info("restored agno default InMemoryRunCancellationManager")
    except Exception as exc:
        logger.warning("Failed to restore default cancellation manager: %s", exc)


async def _shutdown_container(app: FastAPI, resources: _RuntimeResources) -> None:
    """Shutdown app-scoped services before tearing down the underlying infra clients."""
    _restore_default_cancellation_manager()

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

    # Set the level on the root logger to WARNING to avoid noise. This must be done in the lifespan function
    # because uvicorn does something about logging on start up.
    logging.getLogger().setLevel(logging.WARNING)

    logger.info("Starting MCP Gateway Registry")

    try:
        _initialize_telemetry()
        resources = await _startup_container(app)
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
