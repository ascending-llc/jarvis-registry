import asyncio
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from registry_pkgs.oauth import refresh_lock as refresh_lock_module
from registry_pkgs.oauth.errors import OAuthTokenError
from registry_pkgs.oauth.refresh_lock import REFRESH_LOCK_TTL_MS, refresh_lock, refresh_lock_key


class FakeRedis:
    """Just enough of redis.Redis for SET NX PX and the compare-and-delete release script."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[dict[str, Any]] = []
        self.fail_set = False
        self.fail_eval = False

    def set(self, key: str, value: str, *, nx: bool = False, px: int | None = None) -> bool | None:
        self.set_calls.append({"key": key, "nx": nx, "px": px})
        if self.fail_set:
            raise RedisConnectionError("down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        if self.fail_eval:
            raise RedisConnectionError("down")
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refresh_lock_module, "REFRESH_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(refresh_lock_module, "REFRESH_LOCK_WAIT_SECONDS", 0.1)
    refresh_lock_module._memory_locks.clear()


KEY = refresh_lock_key("jarvis", "user-1", "server-1")


def test_key_format() -> None:
    assert KEY == "jarvis:oauth:refresh-lock:user-1:server-1"


@pytest.mark.asyncio
async def test_acquire_and_release() -> None:
    redis = FakeRedis()
    async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
        assert KEY in redis.store
    assert KEY not in redis.store
    assert redis.set_calls[0] == {"key": KEY, "nx": True, "px": REFRESH_LOCK_TTL_MS}


@pytest.mark.asyncio
async def test_contention_waits_for_release() -> None:
    redis = FakeRedis()
    order: list[str] = []

    async def holder() -> None:
        async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            order.append("first-in")
            await asyncio.sleep(0.03)
            order.append("first-out")

    async def waiter() -> None:
        await asyncio.sleep(0.005)
        async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            order.append("second-in")

    await asyncio.gather(holder(), waiter())
    assert order == ["first-in", "first-out", "second-in"]


@pytest.mark.asyncio
async def test_timeout_raises_refresh_in_progress() -> None:
    redis = FakeRedis()
    redis.store[KEY] = "someone-else"
    with pytest.raises(OAuthTokenError, match="refresh already in progress"):
        async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            pass
    assert redis.store[KEY] == "someone-else"


@pytest.mark.asyncio
async def test_release_only_deletes_own_token() -> None:
    redis = FakeRedis()
    async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
        # Our lock expired and another owner took it over.
        redis.store[KEY] = "new-owner"
    assert redis.store[KEY] == "new-owner"


@pytest.mark.asyncio
async def test_acquire_redis_error_propagates() -> None:
    redis = FakeRedis()
    redis.fail_set = True
    with pytest.raises(RedisConnectionError):
        async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            pass


@pytest.mark.asyncio
async def test_release_redis_error_is_swallowed() -> None:
    redis = FakeRedis()
    redis.fail_eval = True
    ran = False
    async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
        ran = True
    assert ran


@pytest.mark.asyncio
async def test_memory_fallback_serializes_and_times_out() -> None:
    order: list[str] = []

    async def holder() -> None:
        async with refresh_lock(None, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            order.append("first-in")
            await asyncio.sleep(0.03)
            order.append("first-out")

    async def waiter() -> None:
        await asyncio.sleep(0.005)
        async with refresh_lock(None, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            order.append("second-in")

    await asyncio.gather(holder(), waiter())
    assert order == ["first-in", "first-out", "second-in"]

    async def long_holder() -> None:
        async with refresh_lock(None, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            await asyncio.sleep(0.3)

    task = asyncio.create_task(long_holder())
    await asyncio.sleep(0.01)
    with pytest.raises(OAuthTokenError, match="refresh already in progress"):
        async with refresh_lock(None, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            pass
    await task
