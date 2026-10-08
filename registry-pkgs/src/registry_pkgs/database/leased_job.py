"""Run one durable job on at most one process at a time, using a Mongo document as the lock.

THE PROBLEM
    Several Registry pods poll the same job collection. A job must execute on exactly one pod, and if
    that pod crashes mid-run another pod must take over — but the crashed pod must never keep writing
    once it has been taken over.

THE MECHANISM — a "lease" on the job document
    A pod *claims* a job by stamping four fields on its document: who holds it (``owner``), a random
    ``token`` minted fresh on every claim, when the hold ``expires``, and the last heartbeat time.
    While it runs, a background heartbeat pushes ``expires`` forward. Every write the pod makes to the
    job is *fenced*: the update only matches ``{_id, token == my token, status is still leased}``. So
    the moment another pod re-claims (which mints a NEW token), the first pod's writes stop matching
    and silently no-op — this is why we fence on the per-claim *token*, not the ``owner``: it works
    even when the SAME pod re-claims its own job.

    If a job's lease expires (the pod crashed and stopped heart-beating), it becomes claimable again.

THE PIECES (top to bottom in this file)
    Data:        LeaseFieldNames / CAMEL_CASE_* / SNAKE_CASE_*  — which Mongo fields hold the lease
                 LeaseSpec                                      — config for one model's lease
                 Lease                                          — a held lease handle given to callers
                 LeaseLostError                                 — "you were taken over; stop writing"
    Mongo:       LeasedRepository      — every fenced read/write for one model's collection
    Async glue:  keep_lease_alive      — the heartbeat loop
                 run_with_lease        — run work + heartbeat together; either one ending stops both
                 interruptible_sleep   — sleep that a stop-event can cut short
    Driver:      LeasedJobRunner       — the poll loop: claim → run under lease → repeat
    Guard:       LeasedDocumentMixin   — makes Beanie ``save()`` raise, so writes can't skip the fence

WIRING IT UP (what a caller writes)
    SPEC = LeaseSpec(document=SkillSyncJob, fields=CAMEL_CASE_LEASE_FIELDS, status_field="status",
                     leased_statuses=frozenset({"syncing"}), duration=timedelta(minutes=2),
                     heartbeat_interval=timedelta(seconds=30))
    repo = LeasedRepository(SPEC)

    async def claim(owner):          # find a runnable job and take its lease
        return await repo.claim(owner=owner, claimable_filter={...}, set_fields={"status": "syncing"})

    async def execute(job, lease):   # do the work; write progress via repo.transition_or_raise(lease, ...)
        ...

    runner = LeasedJobRunner(name="skill-sync", claim=claim, execute=execute, repository=repo)
    await runner.start()             # polls forever until shutdown()

This module must not import from ``registry`` or ``workflow_worker`` so registry, workflow-worker and
later specs can all depend on it. Field names are passed in as data (``LeaseFieldNames``) because the
job models are camelCase while ``WorkflowRun`` is snake_case.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from uuid import uuid4

from beanie import Document, PydanticObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession

logger = logging.getLogger(__name__)


# ======================================================================================
# Data: what a lease is, and which Mongo fields hold it
# ======================================================================================


@dataclass(frozen=True)
class LeaseFieldNames:
    """The Mongo field names a lease reads and writes on a model."""

    owner: str
    token: str
    expires_at: str
    heartbeat_at: str
    updated_at: str


CAMEL_CASE_LEASE_FIELDS = LeaseFieldNames("leaseOwner", "leaseToken", "leaseExpiresAt", "heartbeatAt", "updatedAt")
SNAKE_CASE_LEASE_FIELDS = LeaseFieldNames(
    "lease_owner", "lease_token", "lease_expires_at", "heartbeat_at", "updated_at"
)


@dataclass(frozen=True)
class LeaseSpec[D: Document]:
    """Everything the lease machinery needs to fence one model's collection."""

    document: type[D]
    fields: LeaseFieldNames
    status_field: str
    # The statuses under which a lease is meaningful; part of every fence filter.
    leased_statuses: frozenset[str]
    duration: timedelta
    heartbeat_interval: timedelta
    self_fence_margin: timedelta = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.self_fence_margin is None:
            object.__setattr__(self, "self_fence_margin", self.heartbeat_interval / 2)


@dataclass(frozen=True)
class Lease:
    """A held lease, returned to callers. ``token`` is unique per claim."""

    doc_id: PydanticObjectId
    owner: str
    token: str
    expires_at: datetime


class LeaseLostError(RuntimeError):
    """This process no longer owns the lease and must stop all writes for the document."""


# ======================================================================================
# Mongo: every fenced read/write for one leased model's collection
# ======================================================================================


class LeasedRepository[D: Document]:
    """All Mongo reads/writes for one leased model, each fenced on the per-claim token.

    There are three ways to *take* a lease, for three situations:
      - ``claim``       — poll loop: find any runnable doc and take it (mints a token).
      - ``acquire``     — take the lease on one KNOWN, currently-unleased doc (compare-and-set).
      - ``new_lease`` + ``insert_fields`` — insert a doc that is born already leased by this pod.

    Once held, a lease is used through ``heartbeat`` (renew), ``transition`` / ``transition_or_raise``
    (write progress or finish), ``assert_owned`` (check before expensive work) and, on release,
    ``transition(release=True)``. ``reap_one`` is the janitor: it finalizes a doc whose lease expired.
    Every one of these matches only while this exact token still owns the doc (see ``_fence``).
    """

    def __init__(self, spec: LeaseSpec[D]) -> None:
        self._spec = spec
        self._fields = spec.fields

    @property
    def spec(self) -> LeaseSpec[D]:
        return self._spec

    @property
    def _collection(self):  # type: ignore[no-untyped-def]
        # Resolved per access, not cached in __init__: repository instances are module-level and may
        # be built before init_beanie has registered the collection.
        return self._spec.document.get_pymongo_collection()

    def _fence(self, lease: Lease) -> dict[str, Any]:
        """The filter every held-lease write matches on: same doc, same claim token, still leased.

        A re-claim mints a new token, so this stops matching the instant the lease changes hands —
        that is the whole safety property.
        """
        return {
            "_id": lease.doc_id,
            self._fields.token: lease.token,
            self._spec.status_field: {"$in": list(self._spec.leased_statuses)},
        }

    def _lease_fields(self, owner: str, token: str, expires_at: datetime, now: datetime) -> dict[str, Any]:
        """The four lease fields to ``$set`` when taking or renewing a lease, under this model's names."""
        f = self._fields
        return {f.owner: owner, f.token: token, f.expires_at: expires_at, f.heartbeat_at: now, f.updated_at: now}

    def new_lease(self, owner: str, *, doc_id: PydanticObjectId) -> Lease:
        """Mint a lease without touching Mongo, for a document inserted already leased.

        The caller pre-allocates ``doc_id`` (``PydanticObjectId()``) and passes it both here and as
        the document's ``id=``, so the fence (on ``_id``) matches the inserted document.
        """
        return Lease(doc_id=doc_id, owner=owner, token=uuid4().hex, expires_at=datetime.now(UTC) + self._spec.duration)

    def insert_fields(self, lease: Lease) -> dict[str, Any]:
        """The lease fields, under the model's names, to spread into a new document before ``insert()``."""
        claimed_at = lease.expires_at - self._spec.duration
        return self._lease_fields(lease.owner, lease.token, lease.expires_at, claimed_at)

    async def claim(
        self,
        *,
        owner: str,
        claimable_filter: dict[str, Any],
        set_fields: dict[str, Any] | None = None,
        inc: dict[str, Any] | None = None,
        sort: list[tuple[str, int]] | None = None,
    ) -> tuple[D, Lease] | None:
        """Atomically take the lease on the first doc matching ``claimable_filter`` (mints a token).

        This is the poll loop's entry point. ``claimable_filter`` is the caller's "what counts as
        runnable" (e.g. status PENDING, or a SYNCING job whose lease expired); ``set_fields`` are
        extra fields to write in the same update (e.g. flip status to SYNCING), ``inc`` bumps counters
        (e.g. attempts), ``sort`` picks which one when several match. Returns ``(job, lease)`` or
        ``None`` when nothing was runnable.
        """
        now = datetime.now(UTC)
        token = uuid4().hex
        expires_at = now + self._spec.duration
        update: dict[str, Any] = {"$set": {**(set_fields or {}), **self._lease_fields(owner, token, expires_at, now)}}
        if inc:
            update["$inc"] = inc
        kwargs: dict[str, Any] = {"return_document": ReturnDocument.AFTER}
        if sort is not None:
            kwargs["sort"] = sort
        document = await self._collection.find_one_and_update(claimable_filter, update, **kwargs)
        if document is None:
            return None
        lease = Lease(doc_id=document["_id"], owner=owner, token=token, expires_at=expires_at)
        return self._spec.document.model_validate(document), lease

    async def acquire(
        self,
        *,
        doc_id: PydanticObjectId,
        owner: str,
        expected_filter: dict[str, Any],
        set_fields: dict[str, Any] | None = None,
    ) -> Lease | None:
        """Compare-and-set a lease onto a known, currently-unleased document."""
        now = datetime.now(UTC)
        token = uuid4().hex
        expires_at = now + self._spec.duration
        update = {"$set": {**(set_fields or {}), **self._lease_fields(owner, token, expires_at, now)}}
        document = await self._collection.find_one_and_update(
            {"_id": doc_id, **expected_filter}, update, return_document=ReturnDocument.AFTER
        )
        if document is None:
            return None
        return Lease(doc_id=doc_id, owner=owner, token=token, expires_at=expires_at)

    async def heartbeat(self, lease: Lease) -> Lease | None:
        """Renew the lease while this token still owns it; return the renewed lease, else ``None``."""
        now = datetime.now(UTC)
        expires_at = now + self._spec.duration
        f = self._fields
        result = await self._collection.update_one(
            self._fence(lease), {"$set": {f.expires_at: expires_at, f.heartbeat_at: now, f.updated_at: now}}
        )
        return replace(lease, expires_at=expires_at) if result.modified_count == 1 else None

    async def transition(
        self,
        lease: Lease,
        set_fields: dict[str, Any],
        *,
        unset: list[str] | None = None,
        release: bool = False,
        session: AsyncClientSession | None = None,
    ) -> bool:
        """Apply ``set_fields`` only while this token owns the document; ``release`` clears the lease."""
        now = datetime.now(UTC)
        f = self._fields
        sets: dict[str, Any] = {**set_fields, f.updated_at: now}
        if release:
            sets[f.owner] = None
            sets[f.token] = None
            sets[f.expires_at] = None
        update: dict[str, Any] = {"$set": sets}
        if unset:
            update["$unset"] = dict.fromkeys(unset, "")
        result = await self._collection.update_one(self._fence(lease), update, session=session)
        return result.modified_count == 1

    async def transition_or_raise(
        self,
        lease: Lease,
        set_fields: dict[str, Any],
        *,
        unset: list[str] | None = None,
        release: bool = False,
        session: AsyncClientSession | None = None,
    ) -> None:
        """Like ``transition`` but raise ``LeaseLostError`` when nothing matched."""
        if not await self.transition(lease, set_fields, unset=unset, release=release, session=session):
            raise LeaseLostError(f"lost lease for {self._spec.document.__name__} {lease.doc_id}")

    async def assert_owned(self, lease: Lease) -> None:
        """Raise ``LeaseLostError`` unless this token still owns the document."""
        if await self._collection.find_one(self._fence(lease)) is None:
            raise LeaseLostError(f"lost lease for {self._spec.document.__name__} {lease.doc_id}")

    async def reap_one(self, *, extra_filter: dict[str, Any], set_fields: dict[str, Any]) -> D | None:
        """Finalize one expired leased document, clearing its lease. Not used in this spec."""
        now = datetime.now(UTC)
        f = self._fields
        document = await self._collection.find_one_and_update(
            {
                self._spec.status_field: {"$in": list(self._spec.leased_statuses)},
                f.expires_at: {"$lte": now},
                **extra_filter,
            },
            {"$set": {**set_fields, f.owner: None, f.token: None, f.expires_at: None, f.updated_at: now}},
            return_document=ReturnDocument.AFTER,
        )
        return self._spec.document.model_validate(document) if document is not None else None


# ======================================================================================
# Async glue: keep a lease alive, and couple work to its heartbeat
# ======================================================================================


async def keep_lease_alive[D: Document](repo: LeasedRepository[D], lease: Lease) -> NoReturn:
    """Renew ``lease`` forever, tolerating transient failures until self-fencing forces a stop.

    A single failed heartbeat round-trip must not kill the job, so a raising ``heartbeat`` is logged
    and retried. But a pod that cannot reach Mongo must stop executing *before* its lease expires, or
    it would run concurrently with whoever reaps/re-claims it: once the renewal deadline
    (``expires_at - self_fence_margin``) passes with no successful renewal, raise ``LeaseLostError``.
    """
    spec = repo.spec
    interval = spec.heartbeat_interval.total_seconds()
    current = lease
    while True:
        deadline = current.expires_at - spec.self_fence_margin
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise LeaseLostError(f"could not renew lease for {lease.doc_id} before expiry")
        await asyncio.sleep(min(interval, remaining))
        try:
            renewed = await repo.heartbeat(current)
        except Exception:
            logger.warning("Lease heartbeat for %s failed; will retry", current.doc_id, exc_info=True)
            continue
        if renewed is None:
            raise LeaseLostError(f"lease for {current.doc_id} is no longer held")
        current = renewed


async def run_with_lease[D: Document, T](
    repo: LeasedRepository[D],
    lease: Lease,
    coro: Awaitable[T],
    *,
    on_cancelled: Callable[[], Awaitable[None]] | None = None,
) -> T:
    """Run ``coro`` coupled to a heartbeat; losing the lease cancels it, finishing cancels the heartbeat.

    On outer cancellation (shutdown) ``on_cancelled`` runs **first**, while the execution is still
    running and the lease still held, and only then is the execution cancelled. This lets the caller
    write the terminal state and release the lease so a detached background write (e.g. agno's
    cancelled-run persist) is token-fenced out instead of racing. A raising ``on_cancelled`` is logged
    and does not prevent the cancellation.
    """
    execution: asyncio.Task[T] = asyncio.ensure_future(coro)
    heartbeat: asyncio.Task[NoReturn] = asyncio.ensure_future(keep_lease_alive(repo, lease))
    try:
        done, _pending = await asyncio.wait({execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if execution in done:
            return execution.result()
        _ = await heartbeat  # heartbeat only ever completes by raising LeaseLostError
        raise AssertionError("keep_lease_alive returned without raising")  # pragma: no cover
    except asyncio.CancelledError:
        if on_cancelled is not None:
            try:
                await on_cancelled()
            except Exception:
                logger.exception("Lease on_cancelled handler failed during shutdown")
        raise
    finally:
        for task in (execution, heartbeat):
            if not task.done():
                task.cancel()
        # gather(return_exceptions) both awaits the cancellations and retrieves the exception of a
        # task that already finished by raising (e.g. the heartbeat), so it is never left unretrieved.
        await asyncio.gather(execution, heartbeat, return_exceptions=True)


async def interruptible_sleep(stop_event: asyncio.Event, timeout: float) -> bool:
    """Sleep ``timeout`` seconds, or wake early if ``stop_event`` is set. Return whether it was set."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        return True
    except TimeoutError:
        return False


# ======================================================================================
# Driver: the poll loop that ties claim + run_with_lease together
# ======================================================================================


class LeasedJobRunner[D: Document]:
    """App-scoped poll loop: claim a document, run it under a lease, repeat. One task per runner.

    Each iteration: optional ``before_poll`` housekeeping, then ``claim`` for a job. Nothing claimed →
    sleep one ``poll_interval`` and retry. Claimed → run ``execute(job, lease)`` under a heartbeat via
    ``run_with_lease``. A lost lease is logged and skipped (another owner has it); any other execution
    error goes to ``on_execution_error`` (e.g. to record ``lastError``) and is logged. ``shutdown()``
    cancels the in-flight execution. Callers supply ``claim``/``execute`` so this stays model-agnostic.
    """

    def __init__(
        self,
        *,
        name: str,
        claim: Callable[[str], Awaitable[tuple[D, Lease] | None]],
        execute: Callable[[D, Lease], Awaitable[None]],
        repository: LeasedRepository[D],
        before_poll: Callable[[], Awaitable[None]] | None = None,
        on_execution_error: Callable[[D, Lease, Exception], Awaitable[None]] | None = None,
        poll_interval: timedelta = timedelta(seconds=1),
        owner: str | None = None,
    ) -> None:
        self._name = name
        self._claim = claim
        self._execute = execute
        self._repository = repository
        self._before_poll = before_poll
        self._on_execution_error = on_execution_error
        self._poll_seconds = poll_interval.total_seconds()
        self._owner = owner or f"registry-{uuid4()}"
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def owner(self) -> str:
        return self._owner

    async def start(self) -> None:
        """Start exactly one polling task for this app-scoped runner."""
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=self._name)

    async def shutdown(self) -> None:
        """Stop polling and cancel any in-process execution; safe when never started."""
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            _ = await task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._before_poll is not None:
                    await self._before_poll()
                claimed = await self._claim(self._owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s: claim/poll iteration failed", self._name)
                await interruptible_sleep(self._stop_event, self._poll_seconds)
                continue
            if claimed is None:
                await interruptible_sleep(self._stop_event, self._poll_seconds)
                continue
            job, lease = claimed
            await self._execute_claimed(job, lease)

    async def _execute_claimed(self, job: D, lease: Lease) -> None:
        try:
            await run_with_lease(self._repository, lease, self._execute(job, lease))
        except asyncio.CancelledError:
            raise
        except LeaseLostError:
            # The job belongs to another owner now: do not record an error, just move on.
            logger.warning("%s: lost lease for %s", self._name, lease.doc_id)
            await interruptible_sleep(self._stop_event, self._poll_seconds)
        except Exception as exc:
            if self._on_execution_error is not None:
                try:
                    await self._on_execution_error(job, lease, exc)
                except Exception:
                    logger.exception("%s: on_execution_error handler failed for %s", self._name, lease.doc_id)
            logger.exception("%s: execution failed for %s", self._name, lease.doc_id, exc_info=exc)
            await interruptible_sleep(self._stop_event, self._poll_seconds)


# ======================================================================================
# Guard: stop Beanie whole-document writes from bypassing the fence
# ======================================================================================


class LeasedDocumentMixin:
    """Block whole-document writes on a leased model; writes must go through ``LeasedRepository``.

    ``save()``/``save_changes()``/``replace()`` ``$set`` *every* in-memory field, which silently
    resets the lease fields renewed out-of-band by the heartbeat. ``insert()`` stays allowed for job
    creation.
    """

    def save(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError(f"{type(self).__name__} is lease-fenced; write through LeasedRepository")

    def save_changes(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError(f"{type(self).__name__} is lease-fenced; write through LeasedRepository")

    def replace(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError(f"{type(self).__name__} is lease-fenced; write through LeasedRepository")
