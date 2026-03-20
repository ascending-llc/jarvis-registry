"""Registry entrypoint and application lifecycle wiring."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from registry_pkgs.database import close_mongodb, init_mongodb
from registry_pkgs.database.redis_client import close_redis, init_redis
from registry_pkgs.telemetry import setup_metrics
from registry_pkgs.vector.client import initialize_database

from .app_factory import create_app
from .container import RegistryContainer
from .core.config import settings
from .mcpgw import create_gateway_mcp_app

logger = logging.getLogger(__name__)
app: FastAPI


def _get_current_container() -> RegistryContainer | None:
    return getattr(app.state, "container", None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle management."""
    # Configure logging first before any other operations
    settings.configure_logging()

    logger.info("🚀 Starting MCP Gateway Registry...")

    try:
        logger.info("🔭 Initializing Telemetry...")
        try:
            setup_metrics("mcp-gateway-registry", settings.telemetry_config)
        except Exception as e:
            logger.warning(f"Failed to initialize telemetry: {e}")

        # Initialize MongoDB connection first
        logger.info("🗄️  Initializing MongoDB connection...")
        await init_mongodb(settings.mongo_config)
        logger.info("✅ MongoDB connection established")

        # Initialize Redis connection
        logger.info("🔴 Initializing Redis connection...")
        await init_redis(settings.redis_config)
        logger.info("✅ Redis connection established")

        logger.info("🧭 Initializing vector database client...")
        initialize_database(settings.vector_backend_config)

        container = RegistryContainer(settings=settings)
        app.state.container = container
        await container.startup()

        logger.info("✅ All services initialized successfully!")

    except Exception as e:
        logger.error(f"❌ Failed to initialize services: {e}", exc_info=True)
        raise

    async with gateway_mcp_app.session_manager.run():
        # Application is ready
        yield

    # Shutdown tasks
    logger.info("🔄 Shutting down MCP Gateway Registry...")
    try:
        # Shutdown services gracefully
        container = getattr(app.state, "container", None)
        if container is not None:
            await container.shutdown()
            del app.state.container

        # Close Redis connection
        logger.info("🔴 Closing Redis connection...")
        await close_redis()

        # Close MongoDB connection
        logger.info("🗄️  Closing MongoDB connection...")
        await close_mongodb()

        logger.info("✅ Shutdown completed successfully!")
    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}", exc_info=True)


gateway_mcp_app = create_gateway_mcp_app(container_provider=_get_current_container)
app = create_app(lifespan=lifespan, gateway_mcp_app=gateway_mcp_app)

if __name__ == "__main__":
    # Configure logging before starting server
    settings.configure_logging()

    uvicorn.run(
        "registry.main:app",
        host="0.0.0.0",  # nosec B104 - it's fine to bind to 0.0.0.0 in a container.
        port=7860,
        reload=True,
        log_level=settings.log_level.lower(),
        log_config=None,  # Disable uvicorn's default logging config to use ours
    )
