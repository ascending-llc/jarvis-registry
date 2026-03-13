"""
Centralized Redis connection management for the registry.
Follows the same pattern as MongoDB connection in registry_pkgs/database.
"""

import logging

from redis import Redis

from ..core.config import RedisConfig

# Global Redis client instance
_redis_client: Redis | None = None

logger = logging.getLogger(__name__)


async def init_redis(config: RedisConfig) -> None:
    """
    Initialize Redis connection (called at application startup).
    Similar to init_mongodb() pattern.
    """
    global _redis_client

    if _redis_client is not None:
        logger.warning("Redis client already initialized")
        return

    redis_url = config.redis_uri

    try:
        # Create Redis client with connection pooling
        _redis_client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
            max_connections=50,
        )

        # Test connection
        _redis_client.ping()
        logger.info(f"✅ Successfully connected to Redis: {redis_url}")

    except Exception as e:
        logger.error(f"❌ Failed to connect to Redis at {redis_url}: {e}")
        _redis_client = None
        raise RuntimeError(f"Redis connection failed: {e}")


async def close_redis() -> None:
    """
    Close Redis connection (called at application shutdown).
    Similar to close_mongodb() pattern.
    """
    global _redis_client

    if _redis_client is None:
        logger.warning("Redis client not initialized, nothing to close")
        return

    try:
        _redis_client.close()
        logger.info("✅ Redis connection closed")
        _redis_client = None
    except Exception as e:
        logger.error(f"❌ Error closing Redis connection: {e}")


def get_redis_client() -> Redis | None:
    """
    Get the global Redis client instance.

    Returns:
        Redis client if initialized, None otherwise
    """
    if _redis_client is None:
        logger.warning("Redis client not initialized. Call init_redis() first.")
    return _redis_client
