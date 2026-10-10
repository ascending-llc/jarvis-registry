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


# ---------------------------------------------------------------------------------------------------
# The real WorkflowRun reaper (snake_case lease fields) against real Mongo + Beanie.
# ---------------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def workflow_db():
    """Init Beanie against a throwaway database so WorkflowRun/NodeRun use real collections."""
    from beanie import init_beanie

    from registry_pkgs.models.workflow import NodeRun, WorkflowRun

    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI to run real-Mongo lease tests")
    database_name = f"run_lease_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    await init_beanie(database=client[database_name], document_models=[WorkflowRun, NodeRun])
    try:
        yield client[database_name]
    finally:
        await client.drop_database(database_name)
        await client.close()


async def test_reap_expired_runs_finalizes_expired_and_legacy_but_skips_fresh(workflow_db):
    from registry_pkgs.models.workflow import NodeRun, WorkflowRun
    from registry_pkgs.workflows.run_lease import reap_expired_runs

    runs = WorkflowRun.get_pymongo_collection()
    nodes = NodeRun.get_pymongo_collection()
    now = datetime.now(UTC)
    wf = PydanticObjectId()
    expired, fresh, legacy = PydanticObjectId(), PydanticObjectId(), PydanticObjectId()

    # Expired-lease RUNNING run → reaped by pass 1.
    await runs.insert_one(
        {
            "_id": expired,
            "workflow_definition_id": wf,
            "status": "running",
            "started_at": now,
            "lease_owner": "pod-a",
            "lease_token": "t-exp",
            "lease_expires_at": now - timedelta(seconds=1),
        }
    )
    # Fresh-lease RUNNING run → untouched.
    await runs.insert_one(
        {
            "_id": fresh,
            "workflow_definition_id": wf,
            "status": "running",
            "started_at": now,
            "lease_owner": "pod-b",
            "lease_token": "t-fresh",
            "lease_expires_at": now + timedelta(minutes=5),
        }
    )
    # Lease-less RUNNING run started 10 minutes ago → reaped by pass 2 (legacy cutover).
    await runs.insert_one(
        {
            "_id": legacy,
            "workflow_definition_id": wf,
            "status": "running",
            "started_at": now - timedelta(minutes=10),
        }
    )
    # NodeRuns of the expired run: open ones must be failed, the completed one left alone.
    await nodes.insert_many(
        [
            {
                "_id": PydanticObjectId(),
                "workflow_run_id": expired,
                "node_id": "n1",
                "node_name": "a",
                "status": "running",
            },
            {
                "_id": PydanticObjectId(),
                "workflow_run_id": expired,
                "node_id": "n2",
                "node_name": "b",
                "status": "awaiting_approval",
            },
            {
                "_id": PydanticObjectId(),
                "workflow_run_id": expired,
                "node_id": "n3",
                "node_name": "c",
                "status": "completed",
            },
        ]
    )

    reaped = await reap_expired_runs(legacy_cutover_age=timedelta(seconds=300))

    assert reaped == 2  # expired (pass 1) + legacy (pass 2)

    expired_doc = await runs.find_one({"_id": expired})
    assert expired_doc["status"] == "failed"
    assert expired_doc["error_summary"] == "Executor lost: lease expired"
    assert expired_doc["lease_token"] is None  # lease cleared

    fresh_doc = await runs.find_one({"_id": fresh})
    assert fresh_doc["status"] == "running"  # unexpired lease is untouched
    assert fresh_doc["lease_token"] == "t-fresh"

    legacy_doc = await runs.find_one({"_id": legacy})
    assert legacy_doc["status"] == "failed"
    assert legacy_doc["error_summary"] == "Executor lost: run predates run leasing"

    node_statuses = {doc["node_id"]: doc["status"] async for doc in nodes.find({"workflow_run_id": expired})}
    assert node_statuses == {"n1": "failed", "n2": "failed", "n3": "completed"}


async def test_reap_expired_runs_skips_recent_leaseless_run(workflow_db):
    """Pass 2 must not touch a lease-less run younger than the legacy cutover age."""
    from registry_pkgs.models.workflow import WorkflowRun
    from registry_pkgs.workflows.run_lease import reap_expired_runs

    runs = WorkflowRun.get_pymongo_collection()
    recent = PydanticObjectId()
    await runs.insert_one(
        {
            "_id": recent,
            "workflow_definition_id": PydanticObjectId(),
            "status": "running",
            "started_at": datetime.now(UTC) - timedelta(minutes=1),
        }
    )

    reaped = await reap_expired_runs(legacy_cutover_age=timedelta(seconds=300))

    assert reaped == 0
    assert (await runs.find_one({"_id": recent}))["status"] == "running"
