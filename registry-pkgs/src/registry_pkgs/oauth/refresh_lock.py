"""Per-user, per-server lock serializing OAuth token refreshes and login token writes across pods.

Redis ``SET NX PX`` with a compare-and-delete release. The Redis client is the synchronous
``redis.Redis`` used everywhere in Registry; each command is a single short call bounded by the
client's ``socket_timeout``, which is the same accepted deviation ``RedisFlowStorage`` makes. All
waiting happens in ``asyncio.sleep``; redis-py's blocking ``Lock.acquire`` is never used.

When Redis is unavailable (``FlowStateManager``'s memory fallback), a process-local
``asyncio.Lock`` per key is used instead.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis import Redis
from redis.exceptions import RedisError

from .errors import OAuthTokenError

logger = logging.getLogger(__name__)

REFRESH_LOCK_TTL_MS = 30_000
REFRESH_LOCK_POLL_SECONDS = 0.2
REFRESH_LOCK_WAIT_SECONDS = 10.0

REFRESH_IN_PROGRESS_MESSAGE = "refresh already in progress"

# Delete the key only if it still holds our token, so a lock that expired and was taken over is
# never released by its previous owner.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_memory_locks: dict[str, asyncio.Lock] = {}


def refresh_lock_key(key_prefix: str, user_id: str, server_id: str) -> str:
    return f"{key_prefix}:oauth:refresh-lock:{user_id}:{server_id}"


@asynccontextmanager
async def _memory_lock(key: str) -> AsyncIterator[None]:
    lock = _memory_locks.setdefault(key, asyncio.Lock())
    try:
        await asyncio.wait_for(lock.acquire(), timeout=REFRESH_LOCK_WAIT_SECONDS)
    except TimeoutError as e:
        raise OAuthTokenError(REFRESH_IN_PROGRESS_MESSAGE) from e
    try:
        yield
    finally:
        lock.release()


@asynccontextmanager
async def _redis_lock(redis_client: Redis, key: str) -> AsyncIterator[None]:
    token = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    deadline = loop.time() + REFRESH_LOCK_WAIT_SECONDS
    # A RedisError from SET propagates: callers decide whether to proceed without the lock.
    while not redis_client.set(key, token, nx=True, px=REFRESH_LOCK_TTL_MS):
        if loop.time() >= deadline:
            raise OAuthTokenError(REFRESH_IN_PROGRESS_MESSAGE)
        await asyncio.sleep(REFRESH_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        try:
            redis_client.eval(_RELEASE_SCRIPT, 1, key, token)
        except RedisError as e:
            # The work under the lock is done; the key expires after REFRESH_LOCK_TTL_MS.
            logger.warning("Failed to release OAuth refresh lock %s: %s", key, e)


@asynccontextmanager
async def refresh_lock(
    redis_client: Redis | None, *, key_prefix: str, user_id: str, server_id: str
) -> AsyncIterator[None]:
    """Hold the refresh lock for ``(user_id, server_id)``.

    Raises:
        OAuthTokenError: The lock was still held after ``REFRESH_LOCK_WAIT_SECONDS``.
        redis.exceptions.RedisError: Acquiring the Redis lock failed.
    """
    key = refresh_lock_key(key_prefix, user_id, server_id)
    lock = _memory_lock(key) if redis_client is None else _redis_lock(redis_client, key)
    async with lock:
        yield
