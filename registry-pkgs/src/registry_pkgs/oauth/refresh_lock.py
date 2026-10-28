"""Redis-only refresh leases. Commands run off the event loop, with finite budgets.

A lease coordinates IdP calls; conditional Mongo transactions protect token commits.
It cannot undo a request already processed by an external IdP after lease loss.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .errors import OAuthCoordinationError

logger = logging.getLogger(__name__)

REFRESH_LOCK_TTL_MS = 30_000
REFRESH_LOCK_POLL_SECONDS = 0.2
REFRESH_LOCK_WAIT_SECONDS = 15.0
REFRESH_COMMAND_SECONDS = 2.0
REFRESH_IN_PROGRESS_MESSAGE = "Token refresh already in progress; retry"
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


def refresh_lock_key(key_prefix: str, user_id: str, server_id: str) -> str:
    return f"{key_prefix}:oauth:refresh-lock:{user_id}:{server_id}"


class RefreshLease:
    def __init__(self, redis_client: Redis, key: str, io_limit: asyncio.Semaphore | None = None) -> None:
        self._io_limit = io_limit or asyncio.Semaphore(1)
        self.redis = redis_client
        self.key = key
        self.owner = uuid.uuid4().hex
        self.lost = False
        self._commands: set[asyncio.Task[Any]] = set()

    async def _execute(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        async with self._io_limit:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def command(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        task = asyncio.create_task(self._execute(fn, *args, **kwargs))
        self._commands.add(task)
        try:
            async with asyncio.timeout(REFRESH_COMMAND_SECONDS):
                result = await asyncio.shield(task)
            self._commands.discard(task)
            return result
        except (RedisError, TimeoutError) as exc:
            self.lost = True
            raise OAuthCoordinationError("OAuth coordination unavailable; retry") from exc

    async def ensure_owned(self) -> None:
        if self.lost or not await self.command(
            self.redis.eval, _RENEW_SCRIPT, 1, self.key, self.owner, REFRESH_LOCK_TTL_MS
        ):
            self.lost = True
            raise OAuthCoordinationError("OAuth refresh lease lost; retry")

    async def heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(REFRESH_LOCK_TTL_MS / 3000)
                await self.ensure_owned()
        except OAuthCoordinationError:
            self.lost = True

    async def release(self) -> None:
        # A cancelled/timed-out SET in a thread may still acquire later. Drain it
        # before compare-and-delete; socket timeouts bound the underlying calls.
        if self._commands:
            await asyncio.gather(*self._commands, return_exceptions=True)
            self._commands.clear()
        try:
            await self.command(self.redis.eval, _RELEASE_SCRIPT, 1, self.key, self.owner)
        except OAuthCoordinationError:
            logger.warning("OAuth refresh lock release failed; lease expiry will clean up")
        if self._commands:
            await asyncio.gather(*self._commands, return_exceptions=True)
            self._commands.clear()


@asynccontextmanager
async def refresh_lock(
    redis_client: Redis,
    *,
    key_prefix: str,
    user_id: str,
    server_id: str,
    wait_for_result: bool = False,
    io_limit: asyncio.Semaphore | None = None,
) -> AsyncIterator[RefreshLease | None]:
    if redis_client is None:
        raise OAuthCoordinationError("OAuth refresh requires Redis")
    lease = RefreshLease(redis_client, refresh_lock_key(key_prefix, user_id, server_id), io_limit)
    heartbeat: asyncio.Task[None] | None = None
    try:
        try:
            async with asyncio.timeout(REFRESH_LOCK_WAIT_SECONDS):
                acquired = await lease.command(
                    redis_client.set, lease.key, lease.owner, nx=True, px=REFRESH_LOCK_TTL_MS
                )
                while not acquired:
                    await asyncio.sleep(REFRESH_LOCK_POLL_SECONDS)
                    if wait_for_result:
                        if not await lease.command(redis_client.exists, lease.key):
                            break
                    else:
                        acquired = await lease.command(
                            redis_client.set, lease.key, lease.owner, nx=True, px=REFRESH_LOCK_TTL_MS
                        )
        except TimeoutError as exc:
            raise OAuthCoordinationError(REFRESH_IN_PROGRESS_MESSAGE) from exc
        if not acquired:
            yield None
            return
        heartbeat = asyncio.create_task(lease.heartbeat())
        # No operation budget: cancelling an in-flight IdP refresh could discard a refresh
        # token the IdP already rotated. The OAuth client's HTTP timeout bounds that call,
        # the heartbeat keeps the lease meanwhile, and ensure_owned() guards the commit.
        yield lease
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        cleanup = asyncio.create_task(lease.release())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise
