import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from pymongo import AsyncMongoClient

from registry_pkgs.database.leased_job import CAMEL_CASE_LEASE_FIELDS, LeasedRepository, LeaseSpec

pytestmark = pytest.mark.asyncio

_STATUS = "running"


class _LeasedThing:
    """A throwaway leased document backed by a real test collection."""

    _collection = None

    __name__ = "_LeasedThing"

    @classmethod
    def get_pymongo_collection(cls):
        return cls._collection

    @classmethod
    def model_validate(cls, document):
        from types import SimpleNamespace

        return SimpleNamespace(**document)


def _repo() -> LeasedRepository:
    return LeasedRepository(
        LeaseSpec(
            document=_LeasedThing,
            fields=CAMEL_CASE_LEASE_FIELDS,
            status_field="status",
            leased_statuses=frozenset({_STATUS}),
            duration=timedelta(minutes=2),
            heartbeat_interval=timedelta(seconds=30),
        )
    )


def _claimable(now: datetime) -> dict:
    return {"status": _STATUS, "$or": [{"leaseToken": None}, {"leaseExpiresAt": {"$lte": now}}]}


@pytest_asyncio.fixture
async def collection(monkeypatch):
    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI to run real-Mongo lease tests")
    database_name = f"leased_job_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    coll = client[database_name].leased_things
    monkeypatch.setattr(_LeasedThing, "_collection", coll)
    try:
        yield coll
    finally:
        await client.drop_database(database_name)
        await client.close()


async def _insert_unleased(collection, doc_id: PydanticObjectId) -> None:
    await collection.insert_one({"_id": doc_id, "status": _STATUS, "leaseToken": None, "leaseExpiresAt": None})


async def test_two_owners_race_claim_only_one_wins(collection):
    import asyncio

    repo = _repo()
    doc_id = PydanticObjectId()
    await _insert_unleased(collection, doc_id)

    now = datetime.now(UTC)
    results = await asyncio.gather(
        repo.claim(owner="owner-a", claimable_filter=_claimable(now)),
        repo.claim(owner="owner-b", claimable_filter=_claimable(now)),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


async def test_expired_lease_is_reclaimed_with_a_fresh_token(collection):
    repo = _repo()
    doc_id = PydanticObjectId()
    await _insert_unleased(collection, doc_id)

    first = await repo.claim(owner="owner-a", claimable_filter=_claimable(datetime.now(UTC)))
    assert first is not None
    _doc, lease_a = first

    # Expire owner-a's lease, then owner-b reclaims with a different token.
    await collection.update_one({"_id": doc_id}, {"$set": {"leaseExpiresAt": datetime.now(UTC) - timedelta(seconds=1)}})
    second = await repo.claim(owner="owner-b", claimable_filter=_claimable(datetime.now(UTC)))
    assert second is not None
    _doc_b, lease_b = second
    assert lease_b.token != lease_a.token

    # owner-a's old lease can no longer heartbeat or transition it.
    assert await repo.heartbeat(lease_a) is None
    assert await repo.transition(lease_a, {"status": "done"}) is False


async def test_same_owner_reclaim_rejects_the_old_lease(collection):
    # The token-versus-owner case: even the SAME owner re-claiming invalidates the old Lease object.
    repo = _repo()
    doc_id = PydanticObjectId()
    await _insert_unleased(collection, doc_id)

    first = await repo.claim(owner="owner-a", claimable_filter=_claimable(datetime.now(UTC)))
    assert first is not None
    _doc, lease_old = first

    await collection.update_one({"_id": doc_id}, {"$set": {"leaseExpiresAt": datetime.now(UTC) - timedelta(seconds=1)}})
    second = await repo.claim(owner="owner-a", claimable_filter=_claimable(datetime.now(UTC)))
    assert second is not None
    _doc2, lease_new = second
    assert lease_new.token != lease_old.token

    assert await repo.heartbeat(lease_old) is None
    assert await repo.transition(lease_old, {"status": "done"}) is False
    assert await repo.heartbeat(lease_new) is not None


async def test_reap_one_finalizes_expired_and_skips_unexpired(collection):
    repo = _repo()
    expired, fresh = PydanticObjectId(), PydanticObjectId()
    now = datetime.now(UTC)
    await collection.insert_one(
        {
            "_id": expired,
            "status": _STATUS,
            "leaseOwner": "a",
            "leaseToken": "t",
            "leaseExpiresAt": now - timedelta(seconds=1),
        }
    )
    await collection.insert_one(
        {
            "_id": fresh,
            "status": _STATUS,
            "leaseOwner": "b",
            "leaseToken": "u",
            "leaseExpiresAt": now + timedelta(minutes=5),
        }
    )

    reaped = await repo.reap_one(extra_filter={}, set_fields={"status": "failed"})
    assert reaped is not None
    expired_doc = await collection.find_one({"_id": expired})
    assert expired_doc["status"] == "failed"
    assert expired_doc["leaseToken"] is None
    # The unexpired one is untouched; a second reap finds nothing.
    assert (await collection.find_one({"_id": fresh}))["leaseToken"] == "u"


async def test_release_clears_lease_so_heartbeat_fails(collection):
    repo = _repo()
    doc_id = PydanticObjectId()
    await _insert_unleased(collection, doc_id)

    claimed = await repo.claim(owner="owner-a", claimable_filter=_claimable(datetime.now(UTC)))
    assert claimed is not None
    _doc, lease = claimed

    assert await repo.transition(lease, {"status": "done"}, release=True) is True
    assert await repo.heartbeat(lease) is None
