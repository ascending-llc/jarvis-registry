from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import embedding_reindex_execution_service as exec_module
from registry.services.embedding_reindex_execution_service import (
    EmbeddingReindexExecutionService,
    EmbeddingReindexLeaseLostError,
)
from registry_pkgs.models.enums import EmbeddingReindexJobStatus

pytestmark = pytest.mark.asyncio

COMPLETED = EmbeddingReindexJobStatus.COMPLETED.value
FAILED = EmbeddingReindexJobStatus.FAILED.value


class _AsyncIter:
    """Async-iterable stand-in for a Beanie find_all() cursor; ``raises`` fails during iteration."""

    def __init__(self, items, raises: Exception | None = None):
        self._items = items
        self._raises = raises

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        if self._raises is not None:
            raise self._raises
        for item in self._items:
            yield item


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False  # never suppress: a raise inside aborts + propagates


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def start_transaction(self):
        return _FakeTxn()


def _statuses(transition: AsyncMock) -> list[str]:
    """The status values written via transition_embedding_reindex_job, in order."""
    return [c.kwargs["set_fields"]["status"] for c in transition.await_args_list if "status" in c.kwargs["set_fields"]]


def _errors(transition: AsyncMock) -> list[str]:
    return [c.kwargs["set_fields"]["error"] for c in transition.await_args_list if "error" in c.kwargs["set_fields"]]


def _job(previous_generation=None, *, attempts=1, last_error=None):
    return SimpleNamespace(
        id=PydanticObjectId(),
        targetEmbeddingModelSourceId=PydanticObjectId(),
        previousEmbeddingModelSourceId=None,
        previousCollectionGeneration=previous_generation,
        requestedBy="user-1",
        switchedAt=None,
        attempts=attempts,
        lastError=last_error,
        status=EmbeddingReindexJobStatus.RUNNING,
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

    monkeypatch.setattr(exec_module.ExtendedMCPServer, "find_all", lambda: _AsyncIter(servers))
    monkeypatch.setattr(exec_module.A2AAgent, "find_all", lambda: _AsyncIter(agents))
    monkeypatch.setattr(exec_module, "_drop_collection", MagicMock())

    commit = AsyncMock(return_value=(selection if commit_result == "__ok__" else commit_result))
    monkeypatch.setattr(exec_module, "commit_embedding_generation", commit)

    # Every job write is lease-checked; default to "we own the lease".
    transition = AsyncMock(return_value=True)
    monkeypatch.setattr(exec_module, "transition_embedding_reindex_job", transition)
    monkeypatch.setattr(
        exec_module.MongoDB, "get_client", lambda: SimpleNamespace(start_session=lambda: _FakeSession())
    )
    # The per-batch lease check reads the job; default to "still owned" (truthy doc).
    lease_probe = AsyncMock(return_value={"_id": "held"})
    monkeypatch.setattr(
        exec_module.EmbeddingReindexJob, "get_pymongo_collection", lambda: SimpleNamespace(find_one=lease_probe)
    )

    db_client = MagicMock()
    return SimpleNamespace(
        db_client=db_client,
        mcp_repo=mcp_repo,
        a2a_repo=a2a_repo,
        commit=commit,
        transition=transition,
        lease_probe=lease_probe,
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

    await _service(w.db_client).run_claimed_job(job, lease_owner="worker-1")

    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    w.mcp_repo.ensure_collection.assert_awaited_once()
    w.a2a_repo.ensure_collection.assert_awaited_once()
    # Commit is a compare-and-set on the captured previous generation → the new generation = job id.
    w.commit.assert_awaited_once()
    kwargs = w.commit.await_args.kwargs
    assert kwargs["expected_generation"] is None
    assert kwargs["generation"] == str(job.id)
    assert kwargs["model_source_id"] == job.targetEmbeddingModelSourceId
    assert kwargs["session"] is not None  # committed inside the lease-check transaction
    # The executor NEVER swaps the shared client — every pod follows via its own watcher.
    w.db_client.swap_adapter.assert_not_called()
    w.job_local_client.close.assert_called_once()
    assert COMPLETED in _statuses(w.transition)  # finished via a lease-checked write


async def test_sweep_processes_the_whole_corpus_across_batches(monkeypatch):
    monkeypatch.setattr(exec_module, "_REINDEX_BATCH_SIZE", 2)
    servers = [SimpleNamespace(id=f"s{i}") for i in range(5)]
    mcp_sync = AsyncMock(return_value={"indexed_tools": 1, "failed_tools": 0, "error": None})
    a2a_sync = AsyncMock(return_value={"indexed": 1, "failed": 0, "error": None})
    w = _wire(monkeypatch, servers=servers, agents=[], mcp_sync=mcp_sync, a2a_sync=a2a_sync, current_generation=None)

    await _service(w.db_client).run_claimed_job(_job(previous_generation=None), lease_owner="worker-1")

    assert mcp_sync.await_count == 5  # 2 + 2 + 1 across three batches
    w.commit.assert_awaited_once()
    assert COMPLETED in _statuses(w.transition)


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

    await _service(w.db_client).run_claimed_job(_job(previous_generation=None), lease_owner="worker-1")

    assert mcp_sync.await_count == 2
    assert a2a_sync.await_count == 1
    w.commit.assert_not_awaited()
    w.job_local_client.close.assert_called_once()
    assert FAILED in _statuses(w.transition)
    assert any("1/3" in e for e in _errors(w.transition))


async def test_commit_lost_race_marks_superseded(monkeypatch):
    servers = [SimpleNamespace(id="s1")]
    mcp_sync = AsyncMock(return_value={"indexed_tools": 1, "failed_tools": 0, "error": None})
    a2a_sync = AsyncMock(return_value={"indexed": 1, "failed": 0, "error": None})
    w = _wire(
        monkeypatch,
        servers=servers,
        agents=[],
        mcp_sync=mcp_sync,
        a2a_sync=a2a_sync,
        current_generation=None,
        commit_result=None,  # CAS miss
    )

    await _service(w.db_client).run_claimed_job(_job(previous_generation=None), lease_owner="worker-1")

    w.commit.assert_awaited_once()
    assert FAILED in _statuses(w.transition)
    assert any("uperseded" in e for e in _errors(w.transition))
    w.job_local_client.close.assert_called_once()


async def test_commit_aborts_and_raises_when_lease_lost(monkeypatch):
    # The lease was taken over: the switchedAt transition inside the commit transaction returns False,
    # so the transaction rolls back, the selection is never moved, and lease-loss propagates.
    servers = [SimpleNamespace(id="s1")]
    mcp_sync = AsyncMock(return_value={"indexed_tools": 1, "failed_tools": 0, "error": None})
    w = _wire(monkeypatch, servers=servers, agents=[], mcp_sync=mcp_sync, a2a_sync=AsyncMock(), current_generation=None)
    w.transition.return_value = False  # no longer our lease

    with pytest.raises(EmbeddingReindexLeaseLostError):
        await _service(w.db_client).run_claimed_job(_job(previous_generation=None), lease_owner="worker-1")

    w.commit.assert_not_awaited()  # aborted before the compare-and-set ran
    w.job_local_client.close.assert_called_once()


async def test_lease_loss_between_batches_stops_before_next_batch(monkeypatch):
    monkeypatch.setattr(exec_module, "_REINDEX_BATCH_SIZE", 1)
    servers = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2")]
    mcp_sync = AsyncMock(return_value={"indexed_tools": 1, "failed_tools": 0, "error": None})
    w = _wire(monkeypatch, servers=servers, agents=[], mcp_sync=mcp_sync, a2a_sync=AsyncMock(), current_generation=None)
    # Lease held for the first batch check, gone for the second.
    w.lease_probe.side_effect = [{"_id": "held"}, None]

    with pytest.raises(EmbeddingReindexLeaseLostError):
        await _service(w.db_client).run_claimed_job(_job(previous_generation=None), lease_owner="worker-1")

    assert mcp_sync.await_count == 1  # stopped before the second batch
    w.commit.assert_not_awaited()
    w.job_local_client.close.assert_called_once()


async def test_resume_already_committed_skips_sweep_and_completes(monkeypatch):
    mcp_sync = AsyncMock()
    job = _job(previous_generation=None)
    w = _wire(
        monkeypatch,
        servers=[SimpleNamespace(id="s1")],
        agents=[],
        mcp_sync=mcp_sync,
        a2a_sync=AsyncMock(),
        current_generation=str(job.id),
    )

    await _service(w.db_client).run_claimed_job(job, lease_owner="worker-1")

    mcp_sync.assert_not_awaited()  # no sweep
    w.commit.assert_not_awaited()
    assert COMPLETED in _statuses(w.transition)


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
        current_generation=str(job.id),
    )

    await _service(w.db_client).run_claimed_job(job, lease_owner="worker-1")

    assert COMPLETED in _statuses(w.transition)


async def test_superseded_before_sweep_fails(monkeypatch):
    job = _job(previous_generation="gen0")
    w = _wire(
        monkeypatch, servers=[], agents=[], mcp_sync=AsyncMock(), a2a_sync=AsyncMock(), current_generation="genOTHER"
    )

    await _service(w.db_client).run_claimed_job(job, lease_owner="worker-1")

    w.commit.assert_not_awaited()
    assert FAILED in _statuses(w.transition)
    assert any("uperseded" in e for e in _errors(w.transition))


async def test_missing_target_model_source_fails(monkeypatch):
    w = _wire(monkeypatch, servers=[], agents=[], mcp_sync=AsyncMock(), a2a_sync=AsyncMock(), current_generation=None)
    monkeypatch.setattr(exec_module.ModelSource, "get", AsyncMock(return_value=None))

    await _service(w.db_client).run_claimed_job(_job(previous_generation=None), lease_owner="worker-1")

    w.commit.assert_not_awaited()
    assert FAILED in _statuses(w.transition)


async def test_job_local_client_closed_when_enumeration_fails(monkeypatch):
    w = _wire(monkeypatch, servers=[], agents=[], mcp_sync=AsyncMock(), a2a_sync=AsyncMock(), current_generation=None)
    monkeypatch.setattr(
        exec_module.ExtendedMCPServer, "find_all", lambda: _AsyncIter([], raises=RuntimeError("mongo blip"))
    )

    with pytest.raises(RuntimeError, match="mongo blip"):
        await _service(w.db_client).run_claimed_job(_job(previous_generation=None), lease_owner="worker-1")

    w.job_local_client.close.assert_called_once()
    w.commit.assert_not_awaited()


async def test_finish_exhausted_job_completes_when_already_switched(monkeypatch):
    job = _job()
    w = _wire(
        monkeypatch,
        servers=[],
        agents=[],
        mcp_sync=AsyncMock(),
        a2a_sync=AsyncMock(),
        current_generation=str(job.id),  # the switch already committed
    )

    await _service(w.db_client).finish_exhausted_job(job, lease_owner="worker-1")

    assert COMPLETED in _statuses(w.transition)
    assert FAILED not in _statuses(w.transition)


async def test_finish_exhausted_job_fails_with_last_error_when_not_switched(monkeypatch):
    job = _job(last_error="embed provider 500")
    w = _wire(monkeypatch, servers=[], agents=[], mcp_sync=AsyncMock(), a2a_sync=AsyncMock(), current_generation=None)

    await _service(w.db_client).finish_exhausted_job(job, lease_owner="worker-1")

    assert FAILED in _statuses(w.transition)
    errors = _errors(w.transition)
    assert any("Gave up after 3 attempts" in e and "embed provider 500" in e for e in errors)


def test_drop_collection_skips_when_missing():
    dropped = []
    adapter = SimpleNamespace(collection_exists=lambda name: False, drop_collection=lambda name: dropped.append(name))
    exec_module._drop_collection(SimpleNamespace(write_adapter=adapter), "MCP_Servers_gen")
    assert dropped == []


def test_drop_collection_drops_when_present():
    dropped = []
    adapter = SimpleNamespace(collection_exists=lambda name: True, drop_collection=lambda name: dropped.append(name))
    exec_module._drop_collection(SimpleNamespace(write_adapter=adapter), "MCP_Servers_gen")
    assert dropped == ["MCP_Servers_gen"]


def test_drop_collection_propagates_real_failure():
    def _boom(name):
        raise RuntimeError("weaviate refused")

    adapter = SimpleNamespace(collection_exists=lambda name: True, drop_collection=_boom)
    with pytest.raises(RuntimeError, match="weaviate refused"):
        exec_module._drop_collection(SimpleNamespace(write_adapter=adapter), "MCP_Servers_gen")
