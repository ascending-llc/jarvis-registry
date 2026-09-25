from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import embedding_reindex_execution_service as exec_module
from registry.services.embedding_reindex_execution_service import EmbeddingReindexExecutionService
from registry_pkgs.models.enums import EmbeddingReindexJobStatus

pytestmark = pytest.mark.asyncio


def _job(previous_generation=None):
    return SimpleNamespace(
        id=PydanticObjectId(),
        targetEmbeddingModelSourceId=PydanticObjectId(),
        previousEmbeddingModelSourceId=None,
        previousCollectionGeneration=previous_generation,
        requestedBy="user-1",
        switchedAt=None,
        status=EmbeddingReindexJobStatus.RUNNING,
        error=None,
        finishedAt=None,
        leaseOwner="worker-1",
        leaseExpiresAt=object(),
        save=AsyncMock(),
    )


def _wire(monkeypatch, *, servers, agents, mcp_sync, a2a_sync, current_generation, commit_result="__ok__"):
    """Patch the executor's collaborators. Returns a namespace of the mocks tests assert on."""
    model_source = SimpleNamespace(id=PydanticObjectId(), deletedAt=None)
    selection = SimpleNamespace(embeddingCollectionGeneration=current_generation, embeddingModelSourceId=None)
    monkeypatch.setattr(exec_module, "get_model_gateway_selection", AsyncMock(return_value=selection))
    monkeypatch.setattr(exec_module.ModelSource, "get", AsyncMock(return_value=model_source))
    monkeypatch.setattr(exec_module, "build_backend_config_from_model_source", lambda *a, **k: SimpleNamespace())

    job_local_client = SimpleNamespace(adapter=object(), write_adapter=object(), close=MagicMock())
    monkeypatch.setattr(exec_module, "create_database_client", lambda config: job_local_client)

    mcp_repo = MagicMock()
    mcp_repo.collection = "MCP_Servers_gen"
    mcp_repo.ensure_collection = AsyncMock(return_value=True)
    mcp_repo.sync_to_vector_db = mcp_sync
    a2a_repo = MagicMock()
    a2a_repo.collection = "A2a_agents_gen"
    a2a_repo.ensure_collection = AsyncMock(return_value=True)
    a2a_repo.sync_to_vector_db = a2a_sync
    monkeypatch.setattr(exec_module, "MCPServerRepository", lambda client: mcp_repo)
    monkeypatch.setattr(exec_module, "A2AAgentRepository", lambda client: a2a_repo)

    monkeypatch.setattr(
        exec_module.ExtendedMCPServer, "find_all", lambda: SimpleNamespace(to_list=AsyncMock(return_value=servers))
    )
    monkeypatch.setattr(
        exec_module.A2AAgent, "find_all", lambda: SimpleNamespace(to_list=AsyncMock(return_value=agents))
    )
    monkeypatch.setattr(exec_module, "_drop_collection", MagicMock())

    commit = AsyncMock(return_value=(selection if commit_result == "__ok__" else commit_result))
    monkeypatch.setattr(exec_module, "commit_embedding_generation", commit)

    db_client = MagicMock()
    return SimpleNamespace(
        db_client=db_client,
        mcp_repo=mcp_repo,
        a2a_repo=a2a_repo,
        commit=commit,
        model_source=model_source,
        job_local_client=job_local_client,
    )


def _service(db_client):
    # grace_period 0 so _grace_then_complete never sleeps.
    settings = SimpleNamespace(
        vector_config=SimpleNamespace(), encryption_key=b"key", embedding_reindex_grace_period_seconds=0
    )
    return EmbeddingReindexExecutionService(db_client=db_client, settings=settings)


async def test_happy_path_sweeps_new_generation_commits_and_completes(monkeypatch):
    servers = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2")]
    agents = [SimpleNamespace(id="a1")]
    mcp_sync = AsyncMock(return_value={"indexed_tools": 1, "failed_tools": 0, "error": None})
    a2a_sync = AsyncMock(return_value={"indexed": 1, "failed": 0, "error": None})
    w = _wire(
        monkeypatch, servers=servers, agents=agents, mcp_sync=mcp_sync, a2a_sync=a2a_sync, current_generation=None
    )
    job = _job(previous_generation=None)

    await _service(w.db_client).run_claimed_job(job)

    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    # D1: both generation collections are created up front (even before the sweep inserts).
    w.mcp_repo.ensure_collection.assert_awaited_once()
    w.a2a_repo.ensure_collection.assert_awaited_once()
    # Commit is a compare-and-set on the captured previous generation → the new generation = job id.
    w.commit.assert_awaited_once()
    kwargs = w.commit.await_args.kwargs
    assert kwargs["expected_generation"] is None
    assert kwargs["generation"] == str(job.id)
    assert kwargs["model_source_id"] == job.targetEmbeddingModelSourceId
    assert kwargs["updated_by"] == "user-1"
    # The executor NEVER swaps the shared client — every pod follows via its own watcher.
    w.db_client.swap_adapter.assert_not_called()
    w.job_local_client.close.assert_called_once()
    assert job.switchedAt is not None
    assert job.status == EmbeddingReindexJobStatus.COMPLETED
    assert job.leaseOwner is None


async def test_partial_failure_drops_nothing_committed_and_fails(monkeypatch):
    servers = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2")]
    agents = [SimpleNamespace(id="a1")]
    mcp_sync = AsyncMock(
        side_effect=[
            {"indexed_tools": 1, "failed_tools": 0, "error": None},
            {"indexed_tools": 0, "failed_tools": 1, "error": "embed timeout"},
        ]
    )
    a2a_sync = AsyncMock(return_value={"indexed": 1, "failed": 0, "error": None})
    w = _wire(
        monkeypatch, servers=servers, agents=agents, mcp_sync=mcp_sync, a2a_sync=a2a_sync, current_generation=None
    )
    job = _job(previous_generation=None)

    await _service(w.db_client).run_claimed_job(job)

    # Bounded isolation: every document was attempted.
    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    # No commit on failure → live selection and collections untouched (M2 fixed by generations).
    w.commit.assert_not_awaited()
    w.db_client.swap_adapter.assert_not_called()
    w.job_local_client.close.assert_called_once()
    assert job.status == EmbeddingReindexJobStatus.FAILED
    assert "1/3" in job.error


async def test_commit_lost_race_marks_superseded(monkeypatch):
    servers = [SimpleNamespace(id="s1")]
    mcp_sync = AsyncMock(return_value={"indexed_tools": 1, "failed_tools": 0, "error": None})
    a2a_sync = AsyncMock(return_value={"indexed": 1, "failed": 0, "error": None})
    # CAS returns None: another reindex committed while we swept.
    w = _wire(
        monkeypatch,
        servers=servers,
        agents=[],
        mcp_sync=mcp_sync,
        a2a_sync=a2a_sync,
        current_generation=None,
        commit_result=None,
    )
    job = _job(previous_generation=None)

    await _service(w.db_client).run_claimed_job(job)

    w.commit.assert_awaited_once()
    assert job.status == EmbeddingReindexJobStatus.FAILED
    assert "uperseded" in job.error
    w.job_local_client.close.assert_called_once()


async def test_resume_already_committed_skips_sweep_and_completes(monkeypatch):
    # A crash after commit: the live generation already equals this job's id → straight to grace.
    servers = [SimpleNamespace(id="s1")]
    mcp_sync = AsyncMock()
    a2a_sync = AsyncMock()
    job = _job(previous_generation=None)
    w = _wire(
        monkeypatch,
        servers=servers,
        agents=[],
        mcp_sync=mcp_sync,
        a2a_sync=a2a_sync,
        current_generation=str(job.id),
    )

    await _service(w.db_client).run_claimed_job(job)

    mcp_sync.assert_not_awaited()  # no sweep
    w.commit.assert_not_awaited()
    assert job.status == EmbeddingReindexJobStatus.COMPLETED
    assert job.switchedAt is not None


async def test_resume_completes_with_tz_naive_switched_at(monkeypatch):
    # Mongo returns datetimes tz-naive; the grace math must not crash subtracting from an aware now.
    job = _job(previous_generation=None)
    job.switchedAt = datetime.utcnow()  # naive, as a reloaded document carries it
    w = _wire(
        monkeypatch,
        servers=[],
        agents=[],
        mcp_sync=AsyncMock(),
        a2a_sync=AsyncMock(),
        current_generation=str(job.id),  # resume: already committed -> straight to grace
    )

    await _service(w.db_client).run_claimed_job(job)

    assert job.status == EmbeddingReindexJobStatus.COMPLETED


async def test_superseded_before_sweep_fails(monkeypatch):
    # The live generation is neither ours nor our captured previous → a newer reindex won.
    job = _job(previous_generation="gen0")
    w = _wire(
        monkeypatch,
        servers=[],
        agents=[],
        mcp_sync=AsyncMock(),
        a2a_sync=AsyncMock(),
        current_generation="genOTHER",
    )

    await _service(w.db_client).run_claimed_job(job)

    w.commit.assert_not_awaited()
    assert job.status == EmbeddingReindexJobStatus.FAILED
    assert "uperseded" in job.error


async def test_missing_target_model_source_fails(monkeypatch):
    job = _job(previous_generation=None)
    w = _wire(monkeypatch, servers=[], agents=[], mcp_sync=AsyncMock(), a2a_sync=AsyncMock(), current_generation=None)
    monkeypatch.setattr(exec_module.ModelSource, "get", AsyncMock(return_value=None))

    await _service(w.db_client).run_claimed_job(job)

    w.commit.assert_not_awaited()
    assert job.status == EmbeddingReindexJobStatus.FAILED


async def test_job_local_client_closed_when_enumeration_fails(monkeypatch):
    w = _wire(monkeypatch, servers=[], agents=[], mcp_sync=AsyncMock(), a2a_sync=AsyncMock(), current_generation=None)
    monkeypatch.setattr(
        exec_module.ExtendedMCPServer,
        "find_all",
        lambda: SimpleNamespace(to_list=AsyncMock(side_effect=RuntimeError("mongo blip"))),
    )
    job = _job(previous_generation=None)

    with pytest.raises(RuntimeError, match="mongo blip"):
        await _service(w.db_client).run_claimed_job(job)

    w.job_local_client.close.assert_called_once()
    w.commit.assert_not_awaited()
