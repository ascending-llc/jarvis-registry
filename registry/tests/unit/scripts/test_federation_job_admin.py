from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from scripts import federation_job_admin as admin
from scripts.federation_job_admin import _fail_active_job


@pytest.mark.asyncio
async def test_retry_vector_sync_preflights_before_opening_vector_client(monkeypatch):
    vector_client = MagicMock()
    embedding = MagicMock()
    embedding.embed_query.side_effect = RuntimeError("embedding credentials expired")
    monkeypatch.setattr(admin, "init_mongodb", AsyncMock())
    monkeypatch.setattr(admin, "close_mongodb", AsyncMock())
    monkeypatch.setattr(admin, "get_active_embedding_reindex_job", AsyncMock(return_value=None))
    monkeypatch.setattr(admin, "resolve_vector_backend_config", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(admin.VectorStoreFactory, "_create_embedding", MagicMock(return_value=embedding))
    monkeypatch.setattr(admin, "create_database_client", vector_client)

    with pytest.raises(RuntimeError, match="embedding credentials expired"):
        await admin._retry_vector_sync(ObjectId())

    vector_client.assert_not_called()


@pytest.mark.asyncio
async def test_retry_vector_sync_rebuilds_in_active_generation(monkeypatch, capsys):
    generation = "current-generation"
    vector_client = MagicMock(collection_generation=generation)
    mcp_repo = MagicMock(sync_to_vector_db=AsyncMock(return_value={"indexed_tools": 1}))
    a2a_repo = MagicMock(sync_to_vector_db=AsyncMock(return_value={"indexed": 1}))
    server = SimpleNamespace(serverName="server")
    agent = SimpleNamespace(card=SimpleNamespace(name="agent"))
    collection_query = MagicMock(to_list=AsyncMock())
    federation_id = ObjectId()
    monkeypatch.setattr(admin, "init_mongodb", AsyncMock())
    monkeypatch.setattr(admin, "close_mongodb", AsyncMock())
    monkeypatch.setattr(admin, "get_active_embedding_reindex_job", AsyncMock(return_value=None))
    monkeypatch.setattr(admin, "resolve_vector_backend_config", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(admin.VectorStoreFactory, "_create_embedding", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(admin, "create_database_client", MagicMock(return_value=vector_client))
    monkeypatch.setattr(admin, "MCPServerRepository", MagicMock(return_value=mcp_repo))
    monkeypatch.setattr(admin, "A2AAgentRepository", MagicMock(return_value=a2a_repo))
    monkeypatch.setattr(admin.Federation, "find_one", AsyncMock(return_value=SimpleNamespace(displayName="test")))
    monkeypatch.setattr(admin.ExtendedMCPServer, "find", MagicMock(return_value=collection_query))
    monkeypatch.setattr(admin.A2AAgent, "find", MagicMock(return_value=collection_query))
    collection_query.to_list.side_effect = [[server], [agent]]

    await admin._retry_vector_sync(federation_id)

    mcp_repo.sync_to_vector_db.assert_awaited_once_with(server, is_delete=True)
    a2a_repo.sync_to_vector_db.assert_awaited_once_with(agent, is_delete=True)
    assert f'"collectionGeneration": "{generation}"' in capsys.readouterr().out


def test_fail_active_job_when_federation_is_missing():
    db = MagicMock()
    job_id, federation_id = ObjectId(), ObjectId()
    db.federation_sync_jobs.find_one.return_value = {"_id": job_id, "jobType": "full_sync"}
    db.federation_sync_jobs.update_one.return_value = SimpleNamespace(matched_count=1)
    db.federations.update_one.return_value = SimpleNamespace(matched_count=0)
    db.federations.find_one.return_value = None

    _fail_active_job(db, federation_id, "manual recovery")

    query, update = db.federation_sync_jobs.update_one.call_args.args
    assert query["_id"] == job_id
    assert query["leaseToken"] is None
    assert update["$set"]["status"] == "failed"
    for field in ("leaseOwner", "leaseToken", "leaseExpiresAt", "heartbeatAt"):
        assert update["$set"][field] is None


def test_fail_delete_job_restores_deleting_federation():
    db = MagicMock()
    federation_id = ObjectId()
    job_id = ObjectId()
    db.federation_sync_jobs.find_one.return_value = {"_id": job_id, "jobType": "delete_sync"}
    db.federation_sync_jobs.update_one.return_value = SimpleNamespace(matched_count=1)
    db.federations.update_one.return_value = SimpleNamespace(matched_count=1)

    _fail_active_job(db, federation_id, "manual recovery")

    query, update = db.federations.update_one.call_args.args
    assert query == {
        "_id": federation_id,
        "status": "deleting",
        "deletingJobId": {"$in": [job_id, None]},
    }
    assert update["$set"]["status"] == "active"
    assert update["$set"]["deletingJobId"] is None
    db.federations.update_one.assert_called_once()


def test_fail_active_job_aborts_when_lease_token_changed():
    db = MagicMock()
    job_id, federation_id = ObjectId(), ObjectId()
    db.federation_sync_jobs.find_one.return_value = {
        "_id": job_id,
        "jobType": "full_sync",
        "leaseToken": "original-token",
    }
    db.federation_sync_jobs.update_one.return_value = SimpleNamespace(matched_count=0)

    with pytest.raises(SystemExit, match="Active job changed concurrently"):
        _fail_active_job(db, federation_id, "manual recovery")

    assert db.federation_sync_jobs.update_one.call_args.args[0]["leaseToken"] == "original-token"
    db.federations.update_one.assert_not_called()


def test_fail_sync_job_updates_only_own_pending_parent():
    db = MagicMock()
    job_id, federation_id = ObjectId(), ObjectId()
    db.federation_sync_jobs.find_one.return_value = {"_id": job_id, "jobType": "full_sync"}
    db.federation_sync_jobs.update_one.return_value = SimpleNamespace(matched_count=1)
    db.federations.update_one.return_value = SimpleNamespace(matched_count=1)

    _fail_active_job(db, federation_id, "manual recovery")

    query = db.federations.update_one.call_args.args[0]
    assert query == {
        "_id": federation_id,
        "status": "active",
        "syncStatus": {"$in": ["pending", "syncing"]},
        "lastSync.jobId": {"$in": [job_id, None]},
    }


def test_fail_active_job_reports_parent_owner_change():
    db = MagicMock()
    db.federation_sync_jobs.find_one.return_value = {"_id": ObjectId(), "jobType": "delete_sync"}
    db.federation_sync_jobs.update_one.return_value = SimpleNamespace(matched_count=1)
    db.federations.update_one.return_value = SimpleNamespace(matched_count=0)
    db.federations.find_one.return_value = {"_id": ObjectId(), "deletingJobId": ObjectId()}

    with pytest.raises(SystemExit, match="Federation changed concurrently"):
        _fail_active_job(db, ObjectId(), "manual recovery")


def test_fail_without_active_job_uses_parent_snapshot():
    db = MagicMock()
    federation_id, old_job_id = ObjectId(), ObjectId()
    db.federation_sync_jobs.find_one.return_value = None
    db.federations.find_one.return_value = {
        "_id": federation_id,
        "status": "active",
        "syncStatus": "syncing",
        "lastSync": {"jobId": old_job_id},
    }
    db.federations.update_one.return_value = SimpleNamespace(matched_count=1)

    _fail_active_job(db, federation_id, "manual recovery")

    assert db.federations.update_one.call_args.args[0] == {
        "_id": federation_id,
        "status": "active",
        "syncStatus": "syncing",
        "lastSync.jobId": old_job_id,
        "deletingJobId": None,
    }
