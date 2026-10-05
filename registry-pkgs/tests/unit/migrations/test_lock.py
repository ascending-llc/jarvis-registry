"""Tests for the migration lease lock."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import AutoReconnect, DuplicateKeyError

from registry_pkgs.migrations import lock as lock_module
from registry_pkgs.migrations.lock import (
    LOCK_COLLECTION,
    LOCK_HEARTBEAT_SECONDS,
    LOCK_ID,
    LOCK_TTL_SECONDS,
    POLL_INTERVAL_SECONDS,
    MigrationLock,
    default_owner,
)

_NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
_OWNER = "pod-a:1:deadbeef"


@pytest.fixture
def collection() -> MagicMock:
    return MagicMock(
        find_one_and_update=AsyncMock(return_value=None),
        find_one=AsyncMock(return_value=None),
        update_one=AsyncMock(return_value=MagicMock(matched_count=1)),
        delete_one=AsyncMock(),
    )


@pytest.fixture
def db(collection: MagicMock) -> MagicMock:
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    return database


@pytest.fixture(autouse=True)
def fixed_now() -> None:
    with patch.object(lock_module, "_now", return_value=_NOW):
        yield


def _expected_acquire_call() -> tuple[tuple[dict, dict], dict]:
    return (
        (
            {"_id": LOCK_ID, "$or": [{"expires_at": {"$lte": _NOW}}, {"owner": _OWNER}]},
            {
                "$set": {
                    "owner": _OWNER,
                    "acquired_at": _NOW,
                    "expires_at": _NOW + timedelta(seconds=LOCK_TTL_SECONDS),
                }
            },
        ),
        {"upsert": True},
    )


def test_default_owner_has_host_pid_and_random_suffix() -> None:
    with patch.object(lock_module.socket, "gethostname", return_value="registry-abc"):
        owner = default_owner()

    host, pid, suffix = owner.split(":")
    assert host == "registry-abc"
    assert pid.isdigit()
    assert len(suffix) == 8
    assert default_owner() != default_owner()


@pytest.mark.asyncio
async def test_acquires_when_no_lock_document_exists(db: MagicMock, collection: MagicMock) -> None:
    async with MigrationLock(db, _OWNER) as lock:
        assert not lock.lost

    db.__getitem__.assert_called_with(LOCK_COLLECTION)
    args, kwargs = _expected_acquire_call()
    collection.find_one_and_update.assert_awaited_once_with(*args, **kwargs)


@pytest.mark.asyncio
async def test_acquires_expired_lock_through_the_same_filter(db: MagicMock, collection: MagicMock) -> None:
    # An expired lock matches `expires_at <= now`; Mongo returns the old document and the update takes it over.
    collection.find_one_and_update.return_value = {
        "_id": LOCK_ID,
        "owner": "crashed-pod:9:00000000",
        "expires_at": _NOW - timedelta(seconds=1),
    }

    with patch.object(lock_module.asyncio, "sleep", new_callable=AsyncMock) as sleep:
        async with MigrationLock(db, _OWNER):
            pass

    collection.find_one_and_update.assert_awaited_once()
    filter_ = collection.find_one_and_update.await_args.args[0]
    assert {"expires_at": {"$lte": _NOW}} in filter_["$or"]
    assert POLL_INTERVAL_SECONDS not in [call.args[0] for call in sleep.await_args_list]


@pytest.mark.asyncio
async def test_duplicate_key_means_held_then_retries(
    db: MagicMock, collection: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=lock_module.__name__)
    expires = _NOW + timedelta(seconds=30)
    collection.find_one_and_update.side_effect = [DuplicateKeyError("held"), DuplicateKeyError("held"), None]
    collection.find_one.side_effect = [{"owner": "pod-b:2:cafebabe", "expires_at": expires}, None]

    with patch.object(lock_module.asyncio, "sleep", new_callable=AsyncMock) as sleep:
        async with MigrationLock(db, _OWNER):
            polls = [call.args[0] for call in sleep.await_args_list]

    assert collection.find_one_and_update.await_count == 3
    assert polls == [POLL_INTERVAL_SECONDS, POLL_INTERVAL_SECONDS]
    assert "held by pod-b:2:cafebabe" in caplog.text
    assert str(expires) in caplog.text
    assert "released while acquiring" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_extends_own_lock(db: MagicMock, collection: MagicMock) -> None:
    lock = MigrationLock(db, _OWNER)

    await lock._heartbeat_once()

    collection.update_one.assert_awaited_once_with(
        {"_id": LOCK_ID, "owner": _OWNER},
        {"$set": {"expires_at": _NOW + timedelta(seconds=LOCK_TTL_SECONDS)}},
    )
    assert not lock.lost


@pytest.mark.asyncio
async def test_heartbeat_without_match_sets_lost(db: MagicMock, collection: MagicMock) -> None:
    collection.update_one.return_value = MagicMock(matched_count=0)
    lock = MigrationLock(db, _OWNER)

    await lock._heartbeat_once()

    assert lock.lost


@pytest.mark.asyncio
async def test_heartbeat_error_is_logged_and_next_tick_still_runs(
    db: MagicMock, collection: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    collection.update_one.side_effect = [AutoReconnect("network blip"), MagicMock(matched_count=0)]
    lock = MigrationLock(db, _OWNER)

    with patch.object(lock_module.asyncio, "sleep", new_callable=AsyncMock) as sleep:
        await lock._heartbeat_loop()  # returns once the second tick marks the lock lost

    assert "heartbeat failed" in caplog.text
    assert "network blip" in caplog.text
    assert collection.update_one.await_count == 2
    assert [call.args[0] for call in sleep.await_args_list] == [LOCK_HEARTBEAT_SECONDS, LOCK_HEARTBEAT_SECONDS]
    assert lock.lost


@pytest.mark.asyncio
async def test_heartbeat_error_alone_does_not_set_lost(db: MagicMock, collection: MagicMock) -> None:
    collection.update_one.side_effect = AutoReconnect("down")
    lock = MigrationLock(db, _OWNER)

    await lock._heartbeat_once()

    assert not lock.lost


@pytest.mark.asyncio
async def test_unexpected_heartbeat_error_sets_lost_and_is_logged(
    db: MagicMock, collection: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    collection.update_one.side_effect = RuntimeError("unexpected")
    lock = MigrationLock(db, _OWNER)

    with patch.object(lock_module.asyncio, "sleep", new_callable=AsyncMock):
        await lock._heartbeat_loop()  # returns instead of raising: the error marks the lock lost

    assert lock.lost
    assert collection.update_one.await_count == 1
    assert "heartbeat failed unexpectedly" in caplog.text
    assert "RuntimeError: unexpected" in caplog.text  # traceback logged


@pytest.mark.asyncio
async def test_unexpected_heartbeat_error_still_releases_the_lock(db: MagicMock, collection: MagicMock) -> None:
    collection.update_one.side_effect = RuntimeError("unexpected")

    with patch.object(lock_module, "LOCK_HEARTBEAT_SECONDS", 0):
        async with MigrationLock(db, _OWNER) as lock:
            for _ in range(5):
                await asyncio.sleep(0)
            assert lock.lost
            task = lock._heartbeat_task

    # The task ended without an exception, so __aexit__ did not re-raise it and went on to release.
    assert task is not None and task.done() and not task.cancelled() and task.exception() is None
    collection.delete_one.assert_awaited_once_with({"_id": LOCK_ID, "owner": _OWNER})


@pytest.mark.asyncio
async def test_heartbeat_runs_in_background_while_held(db: MagicMock, collection: MagicMock) -> None:
    with patch.object(lock_module, "LOCK_HEARTBEAT_SECONDS", 0):
        async with MigrationLock(db, _OWNER):
            for _ in range(5):
                await asyncio.sleep(0)

    assert collection.update_one.await_count >= 1


@pytest.mark.asyncio
async def test_release_deletes_only_own_lock_and_stops_heartbeat(db: MagicMock, collection: MagicMock) -> None:
    async with MigrationLock(db, _OWNER) as lock:
        task = lock._heartbeat_task

    collection.delete_one.assert_awaited_once_with({"_id": LOCK_ID, "owner": _OWNER})
    assert task is not None and task.cancelled()


@pytest.mark.asyncio
async def test_release_runs_when_body_raises(db: MagicMock, collection: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with MigrationLock(db, _OWNER):
            raise RuntimeError("boom")

    collection.delete_one.assert_awaited_once_with({"_id": LOCK_ID, "owner": _OWNER})


@pytest.mark.asyncio
async def test_release_failure_is_logged_not_raised(
    db: MagicMock, collection: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    collection.delete_one.side_effect = AutoReconnect("down")

    async with MigrationLock(db, _OWNER):
        pass

    assert "Failed to release migration lock" in caplog.text
