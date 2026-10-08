import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.database.leased_job import (
    CAMEL_CASE_LEASE_FIELDS,
    SNAKE_CASE_LEASE_FIELDS,
    Lease,
    LeasedDocumentMixin,
    LeasedJobRunner,
    LeasedRepository,
    LeaseFieldNames,
    LeaseLostError,
    LeaseSpec,
    interruptible_sleep,
    keep_lease_alive,
    run_with_lease,
)

pytestmark = pytest.mark.asyncio


class _FakeDoc:
    """A throwaway stand-in for a Beanie document: only what the repository touches."""

    __name__ = "_FakeDoc"
    _collection = None

    @classmethod
    def get_pymongo_collection(cls):
        return cls._collection

    @classmethod
    def model_validate(cls, document):
        return SimpleNamespace(**document)


def _spec(**overrides) -> LeaseSpec:
    values = {
        "document": _FakeDoc,
        "fields": CAMEL_CASE_LEASE_FIELDS,
        "status_field": "status",
        "leased_statuses": frozenset({"running"}),
        "duration": timedelta(minutes=5),
        "heartbeat_interval": timedelta(seconds=30),
    }
    values.update(overrides)
    return LeaseSpec(**values)


def _repo(collection, **spec_overrides) -> LeasedRepository:
    _FakeDoc._collection = collection
    return LeasedRepository(_spec(**spec_overrides))


def _lease(doc_id=None) -> Lease:
    return Lease(doc_id=doc_id or PydanticObjectId(), owner="worker-1", token="tok-1", expires_at=datetime.now(UTC))


async def test_field_name_constants():
    assert (
        LeaseFieldNames("leaseOwner", "leaseToken", "leaseExpiresAt", "heartbeatAt", "updatedAt")
        == CAMEL_CASE_LEASE_FIELDS
    )
    assert SNAKE_CASE_LEASE_FIELDS.owner == "lease_owner"
    assert SNAKE_CASE_LEASE_FIELDS.token == "lease_token"


async def test_self_fence_margin_defaults_to_half_the_interval():
    spec = _spec(heartbeat_interval=timedelta(seconds=40))
    assert spec.self_fence_margin == timedelta(seconds=20)


async def test_insert_leased_doc_can_heartbeat_and_transition():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    repo = _repo(collection)
    doc_id = PydanticObjectId()

    lease = repo.new_lease("worker-1", doc_id=doc_id)
    assert lease.doc_id == doc_id and lease.owner == "worker-1" and lease.token

    fields = repo.insert_fields(lease)
    assert fields["leaseOwner"] == "worker-1"
    assert fields["leaseToken"] == lease.token
    assert fields["leaseExpiresAt"] == lease.expires_at

    assert await repo.heartbeat(lease) is not None
    assert await repo.transition(lease, {"status": "done"}) is True


async def test_claim_mints_token_and_returns_model_and_lease():
    doc_id = PydanticObjectId()
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value={"_id": doc_id, "status": "running"})
    repo = _repo(collection)

    result = await repo.claim(owner="worker-1", claimable_filter={"status": "running"}, inc={"attempts": 1})

    assert result is not None
    model, lease = result
    assert model.status == "running"
    assert lease.doc_id == doc_id and lease.owner == "worker-1" and lease.token
    _flt, update = collection.find_one_and_update.await_args.args
    assert update["$set"]["leaseToken"] == lease.token
    assert update["$inc"] == {"attempts": 1}


async def test_claim_returns_none_when_nothing_matched():
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value=None)
    repo = _repo(collection)

    assert await repo.claim(owner="w", claimable_filter={"status": "running"}) is None


async def test_heartbeat_returns_renewed_lease_then_none():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    repo = _repo(collection)
    lease = _lease()

    renewed = await repo.heartbeat(lease)
    assert renewed is not None and renewed.expires_at > lease.expires_at
    assert renewed.token == lease.token

    collection.update_one.return_value = SimpleNamespace(modified_count=0)
    assert await repo.heartbeat(lease) is None


async def test_fence_uses_token_and_status():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    repo = _repo(collection)
    lease = _lease()

    await repo.heartbeat(lease)
    fence = collection.update_one.await_args.args[0]
    assert fence == {"_id": lease.doc_id, "leaseToken": lease.token, "status": {"$in": ["running"]}}


async def test_transition_or_raise_raises_on_miss():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=0))
    repo = _repo(collection)

    with pytest.raises(LeaseLostError):
        await repo.transition_or_raise(_lease(), {"status": "done"})


async def test_transition_release_clears_lease_fields():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))
    repo = _repo(collection)

    await repo.transition(_lease(), {"status": "done"}, release=True)
    sets = collection.update_one.await_args.args[1]["$set"]
    assert sets["leaseOwner"] is None and sets["leaseToken"] is None and sets["leaseExpiresAt"] is None


async def test_assert_owned_raises_when_not_matched():
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    repo = _repo(collection)

    with pytest.raises(LeaseLostError):
        await repo.assert_owned(_lease())


async def test_reap_one_clears_lease_and_applies_fields():
    doc_id = PydanticObjectId()
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value={"_id": doc_id, "status": "failed"})
    repo = _repo(collection)

    result = await repo.reap_one(extra_filter={"sourceId": 1}, set_fields={"status": "failed"})

    assert result is not None
    flt, update = collection.find_one_and_update.await_args.args
    assert flt["status"] == {"$in": ["running"]}
    assert "$lte" in flt["leaseExpiresAt"]
    assert update["$set"]["leaseToken"] is None


def _heartbeat_repo(heartbeat) -> SimpleNamespace:
    # keep_lease_alive only needs spec + heartbeat.
    return SimpleNamespace(
        spec=_spec(duration=timedelta(seconds=0.06), heartbeat_interval=timedelta(seconds=0.01)), heartbeat=heartbeat
    )


async def test_keep_lease_alive_survives_transient_failure_and_keeps_renewing():
    renewals = 0

    async def _heartbeat(current):
        nonlocal renewals
        renewals += 1
        if renewals == 1:
            raise RuntimeError("transient mongo blip")
        # Renew, pushing expiry forward so the loop never self-fences.
        return Lease(
            doc_id=current.doc_id,
            owner=current.owner,
            token=current.token,
            expires_at=datetime.now(UTC) + timedelta(seconds=0.06),
        )

    repo = _heartbeat_repo(AsyncMock(side_effect=_heartbeat))
    lease = Lease(PydanticObjectId(), "w", "t", datetime.now(UTC) + timedelta(seconds=0.06))
    task = asyncio.create_task(keep_lease_alive(repo, lease))
    await asyncio.sleep(0.05)
    assert not task.done()  # survived the transient failure and kept renewing
    assert renewals >= 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task


async def test_keep_lease_alive_raises_when_renewals_fail_until_deadline():
    repo = _heartbeat_repo(AsyncMock(side_effect=RuntimeError("mongo down")))
    lease = Lease(PydanticObjectId(), "w", "t", datetime.now(UTC) + timedelta(seconds=0.06))

    with pytest.raises(LeaseLostError, match="before expiry"):
        await keep_lease_alive(repo, lease)


async def test_keep_lease_alive_raises_immediately_when_heartbeat_returns_none():
    repo = _heartbeat_repo(AsyncMock(return_value=None))
    lease = Lease(PydanticObjectId(), "w", "t", datetime.now(UTC) + timedelta(seconds=0.06))

    with pytest.raises(LeaseLostError, match="no longer held"):
        await keep_lease_alive(repo, lease)


async def _forever():
    await asyncio.Event().wait()


async def test_run_with_lease_returns_execution_result_and_stops_heartbeat():
    heartbeat = AsyncMock(return_value=Lease(PydanticObjectId(), "w", "t", datetime.now(UTC) + timedelta(seconds=1)))
    repo = _heartbeat_repo(heartbeat)

    async def _coro():
        return "done"

    result = await run_with_lease(repo, _lease(), _coro())
    assert result == "done"


async def test_run_with_lease_cancels_execution_when_heartbeat_raises():
    repo = _heartbeat_repo(AsyncMock(return_value=None))  # heartbeat -> lease lost fast
    cancelled = asyncio.Event()

    async def _coro():
        try:
            await _forever()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(LeaseLostError):
        await run_with_lease(repo, _lease(), _coro())
    assert cancelled.is_set()


async def test_run_with_lease_propagates_execution_error():
    heartbeat = AsyncMock(return_value=Lease(PydanticObjectId(), "w", "t", datetime.now(UTC) + timedelta(seconds=1)))
    repo = _heartbeat_repo(heartbeat)

    async def _coro():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await run_with_lease(repo, _lease(), _coro())


async def test_run_with_lease_runs_on_cancelled_before_cancelling_execution():
    heartbeat = AsyncMock(return_value=Lease(PydanticObjectId(), "w", "t", datetime.now(UTC) + timedelta(seconds=1)))
    repo = _heartbeat_repo(heartbeat)
    order: list[str] = []
    started = asyncio.Event()

    async def _coro():
        started.set()
        try:
            await _forever()
        except asyncio.CancelledError:
            order.append("execution_cancelled")
            raise

    async def _on_cancelled():
        order.append("on_cancelled")

    outer = asyncio.create_task(run_with_lease(repo, _lease(), _coro(), on_cancelled=_on_cancelled))
    await started.wait()
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await outer
    assert order == ["on_cancelled", "execution_cancelled"]


async def test_run_with_lease_raising_on_cancelled_still_cancels_and_propagates():
    heartbeat = AsyncMock(return_value=Lease(PydanticObjectId(), "w", "t", datetime.now(UTC) + timedelta(seconds=1)))
    repo = _heartbeat_repo(heartbeat)
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def _coro():
        started.set()
        try:
            await _forever()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _on_cancelled():
        raise RuntimeError("handler blew up")

    outer = asyncio.create_task(run_with_lease(repo, _lease(), _coro(), on_cancelled=_on_cancelled))
    await started.wait()
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await outer
    assert cancelled.is_set()


async def test_runner_keeps_polling_after_lease_lost_without_recording_error():
    lease = _lease()
    heartbeat = AsyncMock(return_value=Lease(lease.doc_id, "w", lease.token, datetime.now(UTC) + timedelta(seconds=1)))
    repo = _heartbeat_repo(heartbeat)
    on_error = AsyncMock()
    calls = 0

    async def _claim(owner):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(id=lease.doc_id), lease
        runner._stop_event.set()
        return None

    async def _execute(job, lease):
        raise LeaseLostError("re-claimed")

    runner = LeasedJobRunner(
        name="t",
        claim=_claim,
        execute=_execute,
        repository=repo,
        on_execution_error=on_error,
        poll_interval=timedelta(seconds=0.01),
    )
    await runner._run()

    on_error.assert_not_awaited()  # a lost lease records nothing
    assert calls >= 2  # kept polling after the loss


async def test_runner_calls_on_execution_error_for_other_failures():
    lease = _lease()
    heartbeat = AsyncMock(return_value=Lease(lease.doc_id, "w", lease.token, datetime.now(UTC) + timedelta(seconds=1)))
    repo = _heartbeat_repo(heartbeat)
    on_error = AsyncMock()
    job = SimpleNamespace(id=lease.doc_id)
    calls = 0

    async def _claim(owner):
        nonlocal calls
        calls += 1
        if calls == 1:
            return job, lease
        runner._stop_event.set()
        return None

    async def _execute(job, lease):
        raise RuntimeError("weaviate down")

    runner = LeasedJobRunner(
        name="t",
        claim=_claim,
        execute=_execute,
        repository=repo,
        on_execution_error=on_error,
        poll_interval=timedelta(seconds=0.01),
    )
    await runner._run()

    on_error.assert_awaited_once()
    assert on_error.await_args.args[0] is job
    assert isinstance(on_error.await_args.args[2], RuntimeError)


async def test_runner_owner_defaults_to_a_registry_uuid():
    runner = LeasedJobRunner(name="t", claim=AsyncMock(), execute=AsyncMock(), repository=MagicMock())
    assert runner.owner.startswith("registry-")


async def test_interruptible_sleep_returns_false_on_timeout():
    assert await interruptible_sleep(asyncio.Event(), 0.01) is False


async def test_interruptible_sleep_returns_true_when_event_set():
    event = asyncio.Event()
    event.set()
    assert await interruptible_sleep(event, 5) is True


async def test_mixin_blocks_whole_document_writes_but_not_insert():
    class _Model(LeasedDocumentMixin):
        pass

    instance = _Model()
    for method in ("save", "save_changes", "replace"):
        with pytest.raises(TypeError, match="lease-fenced"):
            getattr(instance, method)()
    assert "insert" not in LeasedDocumentMixin.__dict__
