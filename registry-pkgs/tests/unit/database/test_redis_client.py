import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest
from redis.asyncio import BlockingConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from registry_pkgs.core.config import RedisConfig
from registry_pkgs.database.redis_client import (
    REDIS_MAX_CONNECTIONS,
    REDIS_POOL_TIMEOUT_SECONDS,
    close_async_redis_client,
    create_async_redis_client,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "uri",
    [
        "redis://localhost:6379/2",
        "redis://localhost:6379/1?db=2&max_connections=75&timeout=20",
    ],
    ids=["default-pool-limits", "uri-cannot-override-pool-limits"],
)
async def test_client_pings_and_owns_its_blocking_pool(monkeypatch: pytest.MonkeyPatch, uri: str) -> None:
    ping = AsyncMock(return_value=True)
    monkeypatch.setattr(Redis, "ping", ping)

    client = await create_async_redis_client(RedisConfig(redis_uri=uri))
    pool = client.connection_pool
    assert isinstance(client, Redis)
    assert isinstance(pool, BlockingConnectionPool)
    assert pool.max_connections == REDIS_MAX_CONNECTIONS == 50
    assert pool.timeout == REDIS_POOL_TIMEOUT_SECONDS == 5
    assert pool.connection_kwargs["db"] == 2
    assert pool.connection_kwargs["decode_responses"] is True
    assert pool.connection_kwargs["socket_connect_timeout"] == 5
    assert pool.connection_kwargs["socket_timeout"] == 5
    assert pool.connection_kwargs["health_check_interval"] == 30
    ping.assert_awaited_once_with()

    disconnect = AsyncMock()
    monkeypatch.setattr(pool, "disconnect", disconnect)
    await close_async_redis_client(client)
    disconnect.assert_awaited_once()


async def test_failed_ping_closes_client_and_preserves_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    error = RedisConnectionError("unavailable")
    monkeypatch.setattr(Redis, "ping", AsyncMock(side_effect=error))
    close = AsyncMock(side_effect=RedisConnectionError("close failed"))
    monkeypatch.setattr(Redis, "aclose", close)

    with pytest.raises(RuntimeError, match="^Redis connection failed: unavailable$") as exc:
        await create_async_redis_client(RedisConfig())
    assert exc.value.__cause__ is error
    close.assert_awaited_once_with()


async def test_cancelled_ping_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Redis, "ping", AsyncMock(side_effect=asyncio.CancelledError))
    close = AsyncMock()
    monkeypatch.setattr(Redis, "aclose", close)

    with pytest.raises(asyncio.CancelledError):
        await create_async_redis_client(RedisConfig())
    close.assert_awaited_once_with()


async def test_close_handles_missing_client_and_logs_close_errors(caplog: pytest.LogCaptureFixture) -> None:
    await close_async_redis_client(None)

    client = Mock(spec=Redis)
    client.aclose.side_effect = RedisConnectionError("close failed")
    with caplog.at_level(logging.ERROR):
        await close_async_redis_client(client)
    client.aclose.assert_awaited_once_with()
    assert "Error closing async Redis connection" in caplog.text
