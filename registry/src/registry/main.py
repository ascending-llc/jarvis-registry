"""Registry entrypoint and application lifecycle wiring."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agno.run.cancel import get_cancellation_manager, set_cancellation_manager
from agno.run.cancellation_management.in_memory_cancellation_manager import InMemoryRunCancellationManager
from fastapi import FastAPI

from registry_pkgs.core.structured_logging import configure_structured_logging
from registry_pkgs.database.mongodb import MongoDB, close_mongodb, init_mongodb
from registry_pkgs.database.redis_client import (
    close_async_redis_client,
    close_redis_client,
    create_async_redis_client,
    create_redis_client,
)
from registry_pkgs.telemetry import setup_metrics, setup_tracing, shutdown_telemetry
from registry_pkgs.telemetry.workflow_metrics import initialize_workflow_metrics
from registry_pkgs.vector.client import create_database_client
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.hitl import MongoBackedCancellationManager

from .app_factory import create_app
from .container import RegistryContainer
from .core.config import settings
from .core.vector_backend import resolve_vector_backend_config
from .mcpgw import create_gateway_mcp_app

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from redis import Redis
    from redis.asyncio import Redis as AsyncRedis

    from registry_pkgs.vector.client import DatabaseClient

    from .mcpgw.core.types import McpAppContext

settings.configure_logging("registry")

logger = logging.getLogger(__name__)

app: FastAPI

gateway_mcp_app: MCPServer[McpAppContext]


def _get_current_container() -> RegistryContainer | None:
    """Return the app-scoped dependency container for route dependencies and MCP tools."""
    return getattr(app.state, "container", None)


def _get_gateway_mcp_app(app: FastAPI):
    """Resolve the mounted MCP gateway from app state, with a module-level fallback for tests."""
    return getattr(app.state, "gateway_mcp_app", gateway_mcp_app)


@dataclass
class _RuntimeResources:
    """Resources acquired by this lifespan, including when startup only partly succeeds."""

    owns_mongodb: bool = False
    redis_client: AsyncRedis | None = None
    sync_redis_client: Redis | None = None
    db_client: DatabaseClient | None = None
    container: RegistryContainer | None = None


def _shutdown_telemetry_safe() -> None:
    """Best-effort telemetry shutdown that never raises."""
    try:
        shutdown_telemetry()
    except Exception:
        logger.warning("Failed to shutdown telemetry", exc_info=True)


def _initialize_telemetry() -> None:
    """Best-effort telemetry setup that should not block the application from starting."""
    logger.info("Initializing telemetry")
    try:
        setup_metrics("mcp-gateway-registry", settings.telemetry_config)
        initialize_workflow_metrics(settings.telemetry_config)
    except Exception:
        logger.warning("Failed to initialize metrics", exc_info=True)
    try:
        setup_tracing("mcp-gateway-registry", settings.telemetry_config)
    except Exception:
        logger.warning("Failed to initialize tracing", exc_info=True)
    try:
        configure_structured_logging(
            "registry",
            "registry_pkgs",
            service_name="mcp-gateway-registry",
            service_version=settings.telemetry_config.build_version,
        )
    except Exception:
        logger.warning("Failed to configure structured logging", exc_info=True)


async def _startup_container(app: FastAPI, resources: _RuntimeResources) -> None:
    """Create infra clients, build the registry container, and expose it on app.state."""
    logger.info("Initializing MongoDB connection")
    resources.owns_mongodb = MongoDB.client is None
    await init_mongodb(settings.mongo_config)

    logger.info("Initializing Redis connection")
    resources.redis_client = await create_async_redis_client(settings.redis_config)
    resources.sync_redis_client = create_redis_client(settings.redis_config)

    logger.info("Initializing vector database client")
    backend_config = await resolve_vector_backend_config(settings)
    resources.db_client = create_database_client(backend_config)

    container = RegistryContainer(
        settings=settings,
        db_client=resources.db_client,
        redis_client=resources.redis_client,
        sync_redis_client=resources.sync_redis_client,
    )
    _install_agno_cancellation_manager(container.directive_queue)
    resources.container = container
    app.state.container = container
    await container.startup()


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
    async with AsyncExitStack() as cleanup:
        if resources.owns_mongodb:
            cleanup.push_async_callback(close_mongodb)
        if resources.db_client is not None:
            cleanup.callback(resources.db_client.close)
        if resources.sync_redis_client is not None:
            cleanup.callback(close_redis_client, resources.sync_redis_client)
        if resources.redis_client is not None:
            cleanup.push_async_callback(close_async_redis_client, resources.redis_client)

        if resources.container is not None:
            _restore_default_cancellation_manager()
            try:
                await resources.container.shutdown()
            finally:
                if getattr(app.state, "container", None) is resources.container:
                    del app.state.container


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the full application lifecycle around one app-scoped dependency container."""

    # Set the level on the root logger to WARNING to avoid noise. This must be done in the lifespan function
    # because uvicorn does something about logging on start up.
    logging.getLogger().setLevel(logging.WARNING)

    logger.info("Starting MCP Gateway Registry")

    resources = _RuntimeResources()
    try:
        _initialize_telemetry()
        try:
            await _startup_container(app, resources)
        except Exception:
            logger.exception("Failed to initialize services")
            raise
        logger.info("Application startup completed")
        async with _get_gateway_mcp_app(app).session_manager.run():
            yield
    finally:
        logger.info("Shutting down MCP Gateway Registry")
        try:
            await _shutdown_container(app, resources)
            logger.info("Application shutdown completed")
        except Exception as exc:
            logger.error("Error during shutdown: %s", exc, exc_info=True)
        finally:
            _shutdown_telemetry_safe()


# The gateway is created once here, but it resolves the active container lazily
# through ``_get_current_container`` so each request uses the current app state.
gateway_mcp_app = create_gateway_mcp_app(container_provider=_get_current_container)

# The FastAPI app is exposed at module level so ASGI servers can import ``app``
# directly while still keeping the startup and shutdown wiring in ``lifespan``.
app = create_app(
    lifespan=lifespan,
    gateway_mcp_app=gateway_mcp_app,
    container_provider=_get_current_container,
)
