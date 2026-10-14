import asyncio

import pytest

from registry_pkgs.oauth import refresh_lock as refresh_lock_module
from registry_pkgs.oauth.errors import OAuthTokenError
from registry_pkgs.oauth.refresh_lock import REFRESH_LOCK_TTL_MS, refresh_lock, refresh_lock_key
from tests.fake_redis import FakeRedis


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refresh_lock_module, "REFRESH_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(refresh_lock_module, "REFRESH_LOCK_WAIT_SECONDS", 0.1)


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
    with pytest.raises(OAuthTokenError):
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
async def test_no_memory_fallback() -> None:
    with pytest.raises(OAuthTokenError, match="requires Redis"):
        async with refresh_lock(None, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            pytest.fail("must not enter")


@pytest.mark.asyncio
async def test_lost_owner_cannot_renew_or_delete_new_owner() -> None:
    redis = FakeRedis()
    async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1") as lease:
        redis.store[KEY] = "new-owner"
        with pytest.raises(OAuthTokenError, match="lease lost"):
            await lease.ensure_owned()
    assert redis.store[KEY] == "new-owner"


@pytest.mark.asyncio
async def test_waiter_observes_result_without_acquiring_second_lease() -> None:
    redis = FakeRedis()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()

    async def waiter() -> None:
        async with refresh_lock(
            redis, key_prefix="jarvis", user_id="user-1", server_id="server-1", wait_for_result=True
        ) as lease:
            assert lease is None

    waiter_task = asyncio.create_task(waiter())
    while len(redis.set_calls) < 2:
        await asyncio.sleep(0)
    release.set()
    await asyncio.gather(task, waiter_task)
    assert len(redis.set_calls) == 2  # Holder's SET and waiter's initial failed SET only.


@pytest.mark.asyncio
async def test_cancelled_holder_releases_lock() -> None:
    redis = FakeRedis()
    entered = asyncio.Event()

    async def holder() -> None:
        async with refresh_lock(redis, key_prefix="jarvis", user_id="user-1", server_id="server-1"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert KEY not in redis.store
