from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import embedding_reindex_execution_service as exec_module
from registry.services.embedding_reindex_execution_service import EmbeddingReindexExecutionService
from registry_pkgs.models.enums import EmbeddingReindexJobStatus

pytestmark = pytest.mark.asyncio


def _job():
    return SimpleNamespace(
        id=PydanticObjectId(),
        targetEmbeddingModelSourceId=PydanticObjectId(),
        status=EmbeddingReindexJobStatus.RUNNING,
        error=None,
        finishedAt=None,
        leaseOwner="worker-1",
        leaseExpiresAt=object(),
        save=AsyncMock(),
    )


def _wire(monkeypatch, *, servers, agents, mcp_sync, a2a_sync, job_local_adapter):
    """Patch the module's collaborators; return the shared live db_client mock."""
    monkeypatch.setattr(exec_module.ModelSource, "get", AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId())))
    monkeypatch.setattr(exec_module, "build_backend_config_from_model_source", lambda *a, **k: SimpleNamespace())

    job_local_client = SimpleNamespace(adapter=job_local_adapter)
    monkeypatch.setattr(exec_module, "create_database_client", lambda config: job_local_client)

    mcp_repo = MagicMock()
    mcp_repo.sync_to_vector_db = mcp_sync
    a2a_repo = MagicMock()
    a2a_repo.sync_to_vector_db = a2a_sync
    monkeypatch.setattr(exec_module, "MCPServerRepository", lambda client: mcp_repo)
    monkeypatch.setattr(exec_module, "A2AAgentRepository", lambda client: a2a_repo)

    monkeypatch.setattr(
        exec_module.ExtendedMCPServer, "find_all", lambda: SimpleNamespace(to_list=AsyncMock(return_value=servers))
    )
    monkeypatch.setattr(
        exec_module.A2AAgent, "find_all", lambda: SimpleNamespace(to_list=AsyncMock(return_value=agents))
    )
    monkeypatch.setattr(exec_module, "_close_adapter", MagicMock())

    db_client = MagicMock()
    return db_client, mcp_repo, a2a_repo


def _service(db_client):
    return EmbeddingReindexExecutionService(
        db_client=db_client,
        settings=SimpleNamespace(vector_config=SimpleNamespace(), encryption_key=b"key"),
    )


async def test_all_documents_reindexed_then_adapter_swapped_and_completed(monkeypatch):
    servers = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2")]
    agents = [SimpleNamespace(id="a1")]
    mcp_sync = AsyncMock(return_value={"indexed_tools": 1, "failed_tools": 0, "error": None})
    a2a_sync = AsyncMock(return_value={"indexed": 1, "failed": 0, "error": None})
    new_adapter = object()
    old_adapter = object()

    db_client, mcp_repo, a2a_repo = _wire(
        monkeypatch, servers=servers, agents=agents, mcp_sync=mcp_sync, a2a_sync=a2a_sync, job_local_adapter=new_adapter
    )
    db_client.swap_adapter = MagicMock(return_value=old_adapter)
    job = _job()

    await _service(db_client).run_claimed_job(job)

    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    db_client.swap_adapter.assert_called_once()
    swapped_adapter, _config = db_client.swap_adapter.call_args.args
    assert swapped_adapter is new_adapter
    exec_module._close_adapter.assert_called_once_with(old_adapter)
    assert job.status == EmbeddingReindexJobStatus.COMPLETED
    assert job.leaseOwner is None


async def test_one_failure_fails_job_and_skips_swap_without_cancelling_siblings(monkeypatch):
    servers = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2")]
    agents = [SimpleNamespace(id="a1")]
    # Second server fails (repo reports failure via its result dict, not by raising).
    mcp_sync = AsyncMock(
        side_effect=[
            {"indexed_tools": 1, "failed_tools": 0, "error": None},
            {"indexed_tools": 0, "failed_tools": 1, "error": "embed timeout"},
        ]
    )
    a2a_sync = AsyncMock(return_value={"indexed": 1, "failed": 0, "error": None})

    db_client, mcp_repo, a2a_repo = _wire(
        monkeypatch, servers=servers, agents=agents, mcp_sync=mcp_sync, a2a_sync=a2a_sync, job_local_adapter=object()
    )
    db_client.swap_adapter = MagicMock()
    job = _job()

    await _service(db_client).run_claimed_job(job)

    # Bounded isolation: every document was attempted, not fail-fast cancelled.
    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    db_client.swap_adapter.assert_not_called()
    exec_module._close_adapter.assert_not_called()
    assert job.status == EmbeddingReindexJobStatus.FAILED
    assert "1/3" in job.error
    assert job.leaseOwner is None


async def test_missing_target_model_source_fails_job(monkeypatch):
    db_client, _mcp, _a2a = _wire(
        monkeypatch,
        servers=[],
        agents=[],
        mcp_sync=AsyncMock(),
        a2a_sync=AsyncMock(),
        job_local_adapter=object(),
    )
    monkeypatch.setattr(exec_module.ModelSource, "get", AsyncMock(return_value=None))
    db_client.swap_adapter = MagicMock()
    job = _job()

    await _service(db_client).run_claimed_job(job)

    db_client.swap_adapter.assert_not_called()
    assert job.status == EmbeddingReindexJobStatus.FAILED
