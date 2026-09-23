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
        triggeredBy="user-1",
        status=EmbeddingReindexJobStatus.RUNNING,
        error=None,
        finishedAt=None,
        leaseOwner="worker-1",
        leaseExpiresAt=object(),
        save=AsyncMock(),
    )


def _wire(monkeypatch, *, servers, agents, mcp_sync, a2a_sync, job_local_adapter):
    """Patch the module's collaborators; return a namespace of the mocks tests assert on."""
    model_source = SimpleNamespace(id=PydanticObjectId())
    monkeypatch.setattr(exec_module.ModelSource, "get", AsyncMock(return_value=model_source))
    monkeypatch.setattr(exec_module, "build_backend_config_from_model_source", lambda *a, **k: SimpleNamespace())

    job_local_client = SimpleNamespace(adapter=job_local_adapter, close=MagicMock())
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
    set_selection = AsyncMock()
    monkeypatch.setattr(exec_module, "set_model_gateway_selection", set_selection)

    return SimpleNamespace(
        db_client=MagicMock(),
        mcp_repo=mcp_repo,
        a2a_repo=a2a_repo,
        set_selection=set_selection,
        model_source=model_source,
        job_local_client=job_local_client,
    )


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

    w = _wire(
        monkeypatch, servers=servers, agents=agents, mcp_sync=mcp_sync, a2a_sync=a2a_sync, job_local_adapter=new_adapter
    )
    w.db_client.swap_adapter = MagicMock(return_value=old_adapter)
    job = _job()

    await _service(w.db_client).run_claimed_job(job)

    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    w.db_client.swap_adapter.assert_called_once()
    swapped_adapter, _config = w.db_client.swap_adapter.call_args.args
    assert swapped_adapter is new_adapter
    exec_module._close_adapter.assert_called_once_with(old_adapter)
    # Selection is committed only after the swap, and carries the job's triggering user for audit.
    w.set_selection.assert_awaited_once_with("embeddingModelSourceId", w.model_source.id, updated_by="user-1")
    # The adapter was handed to container.db_client, so the job-local client must NOT be closed.
    w.job_local_client.close.assert_not_called()
    assert job.status == EmbeddingReindexJobStatus.COMPLETED
    assert job.error is None
    assert job.leaseOwner is None


async def test_partial_failure_still_swaps_and_completes_recording_failed_docs(monkeypatch):
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
    new_adapter = object()
    old_adapter = object()

    w = _wire(
        monkeypatch, servers=servers, agents=agents, mcp_sync=mcp_sync, a2a_sync=a2a_sync, job_local_adapter=new_adapter
    )
    w.db_client.swap_adapter = MagicMock(return_value=old_adapter)
    job = _job()

    await _service(w.db_client).run_claimed_job(job)

    # Bounded isolation: every document was attempted, not fail-fast cancelled.
    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    # F: the collection was already rewritten in place, so the adapter is swapped to match rather than
    # left on the old model (which would serve mixed/wrong results). The job still COMPLETES.
    w.db_client.swap_adapter.assert_called_once()
    swapped_adapter, _config = w.db_client.swap_adapter.call_args.args
    assert swapped_adapter is new_adapter
    exec_module._close_adapter.assert_called_once_with(old_adapter)
    # The swap happened, so the selection is still committed even though some documents failed.
    w.set_selection.assert_awaited_once_with("embeddingModelSourceId", w.model_source.id, updated_by="user-1")
    assert job.status == EmbeddingReindexJobStatus.COMPLETED
    # The failed document is recorded so the partial result is observable and repairable.
    assert "1/3" in job.error
    assert "s2" in job.error
    assert job.leaseOwner is None


async def test_missing_target_model_source_fails_job_without_committing_selection(monkeypatch):
    w = _wire(
        monkeypatch,
        servers=[],
        agents=[],
        mcp_sync=AsyncMock(),
        a2a_sync=AsyncMock(),
        job_local_adapter=object(),
    )
    monkeypatch.setattr(exec_module.ModelSource, "get", AsyncMock(return_value=None))
    w.db_client.swap_adapter = MagicMock()
    job = _job()

    await _service(w.db_client).run_claimed_job(job)

    w.db_client.swap_adapter.assert_not_called()
    # No swap → the gateway selection must NOT be committed (deferred-commit invariant).
    w.set_selection.assert_not_awaited()
    assert job.status == EmbeddingReindexJobStatus.FAILED


async def test_job_local_client_closed_when_enumeration_fails(monkeypatch):
    w = _wire(
        monkeypatch,
        servers=[],
        agents=[],
        mcp_sync=AsyncMock(),
        a2a_sync=AsyncMock(),
        job_local_adapter=object(),
    )
    # find_all() raises AFTER the job-local client has been opened, before the swap.
    monkeypatch.setattr(
        exec_module.ExtendedMCPServer,
        "find_all",
        lambda: SimpleNamespace(to_list=AsyncMock(side_effect=RuntimeError("mongo blip"))),
    )
    w.db_client.swap_adapter = MagicMock()

    with pytest.raises(RuntimeError, match="mongo blip"):
        await _service(w.db_client).run_claimed_job(_job())

    w.db_client.swap_adapter.assert_not_called()
    # The adapter was never promoted, so the job-local client is closed to avoid leaking connections.
    w.job_local_client.close.assert_called_once()
