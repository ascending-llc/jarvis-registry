"""Centralized Redis connection management for the registry."""

import asyncio
import logging

from redis import Redis
from redis.asyncio import BlockingConnectionPool
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.connection import parse_url

from ..core.config import RedisConfig

logger = logging.getLogger(__name__)

REDIS_MAX_CONNECTIONS = 50
REDIS_POOL_TIMEOUT_SECONDS = 5


def create_redis_client(config: RedisConfig) -> Redis:
    """Create a Redis client without touching module-level global state."""
    redis_url = config.redis_uri

    try:
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
            max_connections=50,
        )

        # Test connection
        client.ping()
        logger.info(f"✅ Successfully connected to Redis: {redis_url}")
        return client

    except Exception as e:
        logger.error(f"❌ Failed to connect to Redis at {redis_url}: {e}")
        raise RuntimeError(f"Redis connection failed: {e}")


def close_redis_client(client: Redis | None) -> None:
    """Close a Redis client created by create_redis_client()."""
    if client is None:
        return
    try:
        client.close()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.error("Error closing Redis connection: %s", e)


async def create_async_redis_client(config: RedisConfig) -> AsyncRedis:
    """Return a connected client that owns its bounded, waiting connection pool."""
    client = None
    try:
        # from_url gives the URI precedence over all kwargs. Only the two pool limits are fixed here.
        options = {
            "decode_responses": True,
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
            "health_check_interval": 30,
            **parse_url(config.redis_uri),
            "max_connections": REDIS_MAX_CONNECTIONS,
            "timeout": REDIS_POOL_TIMEOUT_SECONDS,
        }
        pool = BlockingConnectionPool(**options)
        client = AsyncRedis.from_pool(pool)
        await client.ping()
    except asyncio.CancelledError:
        await close_async_redis_client(client)
        raise
    except Exception as exc:
        await close_async_redis_client(client)
        raise RuntimeError(f"Redis connection failed: {exc}") from exc
    return client


async def close_async_redis_client(client: AsyncRedis | None) -> None:
    """Release the client's connections without interrupting other shutdown steps."""
    if client is None:
        return
    try:
        await client.aclose()
    except Exception as exc:
        logger.error("Error closing async Redis connection: %s", exc)
