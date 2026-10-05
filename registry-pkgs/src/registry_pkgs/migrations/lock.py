"""Lease lock that lets exactly one migration runner apply migrations at a time.

The lock is one document in `registry_migrations_lock`. Its holder renews `expires_at` from a background
heartbeat; a crashed holder blocks other runners for at most `LOCK_TTL_SECONDS`. The lock excludes other
runners only, never application writes.
"""

import asyncio
import contextlib
import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import uuid4

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

logger = logging.getLogger(__name__)

LOCK_COLLECTION = "registry_migrations_lock"
LOCK_ID = "migrations"
LOCK_TTL_SECONDS = 60
LOCK_HEARTBEAT_SECONDS = 15
POLL_INTERVAL_SECONDS = 5


def _now() -> datetime:
    # Client clock: pod clocks are NTP-synced, and the TTL leaves a wide margin.
    return datetime.now(UTC)


def default_owner() -> str:
    """`<hostname>:<pid>:<random>`; the hostname is the pod name in k8s."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


class MigrationLock:
    """Async context manager: acquire (waiting while another owner holds it), heartbeat, release.

    `lost` becomes True when a heartbeat finds the lock no longer names this owner. The holder must check it
    before starting each migration and before writing each record.
    """

    def __init__(self, db: AsyncDatabase, owner: str) -> None:
        self._collection: AsyncCollection = db[LOCK_COLLECTION]
        self.owner = owner
        self._lost = False
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def lost(self) -> bool:
        return self._lost

    async def _try_acquire(self) -> bool:
        now = _now()
        try:
            await self._collection.find_one_and_update(
                {"_id": LOCK_ID, "$or": [{"expires_at": {"$lte": now}}, {"owner": self.owner}]},
                {
                    "$set": {
                        "owner": self.owner,
                        "acquired_at": now,
                        "expires_at": now + timedelta(seconds=LOCK_TTL_SECONDS),
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # The filter did not match an unexpired lock held by someone else, so the upsert collided on _id.
            holder = await self._collection.find_one({"_id": LOCK_ID})
            if holder is None:
                logger.info("Migration lock was released while acquiring; retrying")
            else:
                logger.info(
                    "Migration lock held by %s until %s; retrying in %ss",
                    holder.get("owner"),
                    holder.get("expires_at"),
                    POLL_INTERVAL_SECONDS,
                )
            return False
        return True

    async def _heartbeat_once(self) -> None:
        try:
            result = await self._collection.update_one(
                {"_id": LOCK_ID, "owner": self.owner},
                {"$set": {"expires_at": _now() + timedelta(seconds=LOCK_TTL_SECONDS)}},
            )
        except PyMongoError as exc:
            # Retried at the next tick. If another runner takes the lock meanwhile, that tick sees no match.
            logger.warning("Migration lock heartbeat failed; retrying in %ss: %s", LOCK_HEARTBEAT_SECONDS, exc)
            return
        if result.matched_count == 0:
            self._lost = True
            logger.error("Migration lock lost: it no longer names owner %s", self.owner)

    async def _heartbeat_loop(self) -> None:
        while not self._lost:
            await asyncio.sleep(LOCK_HEARTBEAT_SECONDS)
            await self._heartbeat_once()

    async def __aenter__(self) -> Self:
        while not await self._try_acquire():
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        logger.info("Acquired migration lock as %s", self.owner)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        try:
            await self._collection.delete_one({"_id": LOCK_ID, "owner": self.owner})
        except PyMongoError as release_exc:
            # Not fatal: the lease expires on its own after LOCK_TTL_SECONDS.
            logger.warning("Failed to release migration lock; it expires in %ss: %s", LOCK_TTL_SECONDS, release_exc)
            return
        logger.info("Released migration lock")
