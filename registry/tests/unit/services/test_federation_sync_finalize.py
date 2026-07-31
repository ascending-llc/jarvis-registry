"""Tests for federation sync finalization, statistics, and vector updates."""

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation_sync_service import (
    FederationSyncMutationResult,
    FederationSyncService,
    VectorSyncOutcome,
)
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobStatus,
    FederationProviderType,
    FederationSyncStatus,
)
from registry_pkgs.models.federation_sync_job import FederationApplySummary
from tests.unit.services.federation_sync_test_helpers import (
    _DEFAULT_USER_OBJECT_ID,
    _FakeQuery,
    _make_federation,
    _patch_mongo_session,
)

pytestmark = pytest.mark.usefixtures("default_empty_access_roles")


@pytest.mark.asyncio
async def test_finalize_marks_success_with_error_message_when_some_resources_imported(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """When apply has errors but importedTotal > 0, finalize marks SUCCESS with the error message."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=2, discoveredAgents=1),
    )
    apply_error = "A2A agent wip-agent: a2a enrichment failed: connection timeout"
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(
            createdMcpServers=2,
            unchangedAgents=0,
            errors=1,
            errorMessages=[apply_error],
        ),
    )
    vector_outcome = VectorSyncOutcome()

    mock_stats = SimpleNamespace(importedTotal=2, unimportedTotal=1)
    federation_sync_service._build_federation_stats = AsyncMock(return_value=mock_stats)
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    await federation_sync_service._finalize_sync_status(federation, job, mutation_result, vector_outcome)

    federation_sync_service.federation_crud_service.mark_sync_success.assert_awaited_once()
    call_kwargs = federation_sync_service.federation_crud_service.mark_sync_success.await_args
    assert call_kwargs.kwargs["message"] == apply_error
    federation_sync_service.federation_job_service.mark_success.assert_awaited_once()
    assert federation_sync_service.federation_job_service.mark_success.await_args.kwargs["message"] == apply_error
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_combines_apply_and_vector_errors_into_summary(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """Finalize merges vector outcome errors into apply_summary before building stats."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=2, discoveredAgents=1),
    )
    apply_error = "A2A agent wip-agent: a2a enrichment failed: connection timeout"
    vector_error = "mcp runtime rebuild failed:fed:arn:mcp:1:weaviate timeout"
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(
            createdMcpServers=2,
            errors=1,
            errorMessages=[apply_error],
        ),
    )
    vector_outcome = VectorSyncOutcome(
        failed_changed_mcp_runtime_arns={"arn:mcp:1"},
        error_messages=[vector_error],
    )

    mock_stats = SimpleNamespace(importedTotal=1, unimportedTotal=2)
    federation_sync_service._build_federation_stats = AsyncMock(return_value=mock_stats)
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    await federation_sync_service._finalize_sync_status(federation, job, mutation_result, vector_outcome)

    assert mutation_result.summary.errors == 2
    assert mutation_result.summary.errorMessages == [apply_error, vector_error]
    assert mutation_result.summary.vectorSyncFailedMcpServers == 1
    federation_sync_service.federation_crud_service.mark_sync_success.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_sync_remains_active_and_rejects_second_sync_during_vector_tail(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    federation.syncStatus = FederationSyncStatus.SYNCING
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        status=FederationJobStatus.SYNCING,
        phase=FederationJobPhase.APPLYING,
        startedAt=datetime.now(UTC),
    )
    apply_error = "A2A agent wip-agent: enrichment failed"
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(errors=1, errorMessages=[apply_error]),
    )
    vector_started = asyncio.Event()
    release_vector = asyncio.Event()

    async def _mark_syncing(current_job, phase):
        current_job.status = FederationJobStatus.SYNCING
        current_job.phase = phase
        return current_job

    async def _block_vector_sync(**_kwargs):
        vector_started.set()
        await release_vector.wait()
        return VectorSyncOutcome()

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_bookkeeping_transaction = AsyncMock(
        return_value=SimpleNamespace(summary=mutation_result.summary)
    )
    federation_sync_service._apply_sync_plan = AsyncMock(return_value=mutation_result)
    federation_sync_service.federation_job_service.update_apply_summary = AsyncMock()
    federation_sync_service._sync_vector_index_after_commit = AsyncMock(side_effect=_block_vector_sync)
    federation_sync_service._finalize_sync_status = AsyncMock()
    federation_sync_service.federation_job_service.mark_syncing = AsyncMock(side_effect=_mark_syncing)
    federation_sync_service.federation_job_service.get_active_job = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    _patch_mongo_session(monkeypatch)

    sync_task = asyncio.create_task(
        federation_sync_service.run_sync(
            federation=federation,
            job=job,
            author_id=_DEFAULT_USER_OBJECT_ID,
        )
    )
    await asyncio.wait_for(vector_started.wait(), timeout=1)

    try:
        assert job.status == FederationJobStatus.SYNCING
        assert job.phase == FederationJobPhase.SYNCING_VECTORS
        assert federation.syncStatus == FederationSyncStatus.SYNCING
        federation_sync_service.federation_job_service.mark_failed.assert_not_awaited()
        federation_sync_service.federation_job_service.get_active_job.return_value = job
        with pytest.raises(ValueError, match="already has an active sync job"):
            await federation_sync_service.create_manual_sync_job(
                federation=federation,
                reason="second sync",
                triggered_by="user-1",
            )
    finally:
        release_vector.set()
        await sync_task


@pytest.mark.asyncio
async def test_run_sync_updates_last_sync_when_discovery_fails(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=None,
    )

    federation_sync_service._discover_entities = AsyncMock(side_effect=RuntimeError("discovery failed"))
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    with pytest.raises(RuntimeError, match="discovery failed"):
        await federation_sync_service.run_sync(federation=federation, job=job, author_id=_DEFAULT_USER_OBJECT_ID)

    federation_sync_service.federation_crud_service.mark_sync_failed.assert_awaited_once()
    failed_last_sync = federation_sync_service.federation_crud_service.mark_sync_failed.await_args.kwargs["last_sync"]
    assert failed_last_sync.status == FederationSyncStatus.FAILED
    assert failed_last_sync.summary is not None
    assert failed_last_sync.summary.errorMessages == ["discovery failed"]


@pytest.mark.asyncio
async def test_sync_vector_index_after_commit_returns_outcome_with_failures(
    federation_sync_service: FederationSyncService,
    caplog,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(createdMcpServers=1, deletedAgents=1),
        changed_mcp_runtime_arns={"arn:mcp:1"},
        changed_a2a_runtime_arns={"arn:a2a:1"},
    )

    federation_sync_service._sync_mcp_vectors_for_runtime = AsyncMock(side_effect=RuntimeError("vector down"))
    federation_sync_service._sync_a2a_vectors_for_runtime = AsyncMock(side_effect=RuntimeError("vector unavailable"))
    federation_sync_service._current_mcp_runtime_arns = AsyncMock(return_value=[])
    federation_sync_service._current_a2a_runtime_arns = AsyncMock(return_value=[])
    caplog.set_level(logging.ERROR, logger="registry.services.federation_sync_service")

    outcome = await federation_sync_service._sync_vector_index_after_commit(
        federation=federation,
        job=job,
        mutation_result=mutation_result,
    )

    assert isinstance(outcome, VectorSyncOutcome)
    assert "arn:mcp:1" in outcome.failed_changed_mcp_runtime_arns
    assert "arn:a2a:1" in outcome.failed_changed_a2a_runtime_arns
    assert len(outcome.error_messages) == 2
    assert "vector down" in outcome.error_messages[0]
    assert "vector unavailable" in outcome.error_messages[1]
    federation_sync_service._sync_mcp_vectors_for_runtime.assert_awaited_once_with(federation.id, "arn:mcp:1")
    federation_sync_service._sync_a2a_vectors_for_runtime.assert_awaited_once_with(federation.id, "arn:a2a:1")
    error_records = [
        record
        for record in caplog.records
        if record.message.startswith(("MCP runtime vector rebuild failed", "A2A runtime vector rebuild failed"))
    ]
    assert len(error_records) == 2
    assert all(record.exc_info is not None for record in error_records)
    assert all(f"federation_id={federation.id}" in record.message for record in error_records)
    assert all(f"job_id={job.id}" in record.message for record in error_records)
    assert any("runtime_arn=arn:mcp:1" in record.message for record in error_records)
    assert any("runtime_arn=arn:a2a:1" in record.message for record in error_records)


@pytest.mark.asyncio
async def test_sync_vector_index_after_commit_rebuilds_only_changed_runtimes(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(createdMcpServers=1, updatedAgents=1),
        changed_mcp_runtime_arns={"arn:mcp:1"},
        changed_a2a_runtime_arns={"arn:a2a:1"},
    )

    federation_sync_service._sync_mcp_vectors_for_runtime = AsyncMock()
    federation_sync_service._sync_a2a_vectors_for_runtime = AsyncMock()
    federation_sync_service._current_mcp_runtime_arns = AsyncMock(return_value=[])
    federation_sync_service._current_a2a_runtime_arns = AsyncMock(return_value=[])

    await federation_sync_service._sync_vector_index_after_commit(
        federation=federation,
        job=job,
        mutation_result=mutation_result,
    )

    federation_sync_service._sync_mcp_vectors_for_runtime.assert_awaited_once_with(federation.id, "arn:mcp:1")
    federation_sync_service._sync_a2a_vectors_for_runtime.assert_awaited_once_with(federation.id, "arn:a2a:1")


@pytest.mark.asyncio
async def test_sync_vector_index_after_commit_rebuilds_missing_weaviate_docs_even_without_mongo_changes(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    mutation_result = FederationSyncMutationResult(summary=FederationApplySummary())

    federation_sync_service._current_mcp_runtime_arns = AsyncMock(return_value=["arn:mcp:missing"])
    federation_sync_service._current_a2a_runtime_arns = AsyncMock(return_value=["arn:a2a:missing"])
    federation_sync_service.mcp_server_repo.has_runtime_identity.return_value = False
    federation_sync_service.a2a_agent_repo.has_runtime_identity.return_value = False
    federation_sync_service._sync_mcp_vectors_for_runtime = AsyncMock()
    federation_sync_service._sync_a2a_vectors_for_runtime = AsyncMock()

    await federation_sync_service._sync_vector_index_after_commit(
        federation=federation,
        job=job,
        mutation_result=mutation_result,
    )

    federation_sync_service._sync_mcp_vectors_for_runtime.assert_awaited_once_with(federation.id, "arn:mcp:missing")
    federation_sync_service._sync_a2a_vectors_for_runtime.assert_awaited_once_with(federation.id, "arn:a2a:missing")


@pytest.mark.asyncio
async def test_sync_vector_index_after_commit_logs_summary_when_nothing_to_rebuild(
    federation_sync_service: FederationSyncService,
    caplog,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    mutation_result = FederationSyncMutationResult(summary=FederationApplySummary())

    federation_sync_service._current_mcp_runtime_arns = AsyncMock(return_value=["arn:mcp:1"])
    federation_sync_service._current_a2a_runtime_arns = AsyncMock(return_value=["arn:a2a:1"])
    federation_sync_service.mcp_server_repo.has_runtime_identity.return_value = True
    federation_sync_service.a2a_agent_repo.has_runtime_identity.return_value = True
    federation_sync_service._sync_mcp_vectors_for_runtime = AsyncMock()
    federation_sync_service._sync_a2a_vectors_for_runtime = AsyncMock()

    registry_logger = logging.getLogger("registry")
    registry_logger.addHandler(caplog.handler)

    try:
        with caplog.at_level("INFO", logger="registry"):
            await federation_sync_service._sync_vector_index_after_commit(
                federation=federation,
                job=job,
                mutation_result=mutation_result,
            )
    finally:
        registry_logger.removeHandler(caplog.handler)

    assert "Federation vector sync plan" in caplog.text
    assert "mcp_rebuild=0" in caplog.text
    assert "a2a_rebuild=0" in caplog.text
    assert "Federation vector sync completed" in caplog.text


@pytest.mark.asyncio
async def test_bookkeeping_transaction_returns_plan_with_enrichment_errors(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=1, discoveredAgents=1),
    )
    summary = FederationApplySummary(errors=1, errorMessages=["A2A agent pharmacy_fraud_a2a: boom"])
    plan = SimpleNamespace(
        summary=summary,
        discovered_mcp_count=1,
        discovered_a2a_count=1,
    )

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_crud_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.update_discovery_summary = AsyncMock()
    federation_sync_service._build_sync_plan = AsyncMock(return_value=plan)
    session = object()

    result = await federation_sync_service._commit_bookkeeping_transaction(
        federation=federation,
        job=job,
        discovered={"mcp_servers": [SimpleNamespace()], "a2a_agents": [SimpleNamespace()]},
        session=session,
    )

    assert result == plan
    assert result.summary.errorMessages == ["A2A agent pharmacy_fraud_a2a: boom"]
    assert federation_sync_service.federation_crud_service.mark_syncing.await_args.kwargs["last_sync"].status == (
        FederationSyncStatus.SYNCING
    )


def test_build_pending_last_sync_uses_pending_status():
    now = datetime.now(UTC)
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=now,
        startedAt=None,
    )

    last_sync = FederationSyncService._build_pending_last_sync(job)

    assert last_sync.status == FederationSyncStatus.PENDING
    assert last_sync.startedAt == now


def test_build_failed_last_sync_adds_error_summary():
    now = datetime.now(UTC)
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=now,
        startedAt=None,
    )

    last_sync = FederationSyncService._build_failed_last_sync(job, "discovery failed")

    assert last_sync.status == FederationSyncStatus.FAILED
    assert last_sync.summary is not None
    assert last_sync.summary.errors == 1
    assert last_sync.summary.errorMessages == ["discovery failed"]


def test_build_last_sync_carries_error_count_with_success_placeholder():
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=1, discoveredAgents=2),
    )
    summary = FederationApplySummary(
        unchangedMcpServers=1,
        unchangedAgents=2,
        errors=1,
        errorMessages=["A2A agent pharmacy_fraud_a2a: boom"],
    )

    last_sync = FederationSyncService._build_last_sync(job, summary)

    assert last_sync.status == FederationSyncStatus.SUCCESS
    assert last_sync.summary.errors == 1
    assert last_sync.summary.errorMessages == ["A2A agent pharmacy_fraud_a2a: boom"]


def test_build_last_sync_treats_skip_only_summary_as_success():
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=2, discoveredAgents=1),
    )
    summary = FederationApplySummary(
        skippedMcpServers=2,
        skippedAgents=1,
        errors=0,
        errorMessages=[],
    )

    last_sync = FederationSyncService._build_last_sync(job, summary)

    assert last_sync.status == FederationSyncStatus.SUCCESS
    assert last_sync.summary.skippedMcpServers == 2
    assert last_sync.summary.skippedAgents == 1
    assert last_sync.summary.errors == 0


@pytest.mark.asyncio
async def test_finalize_marks_failed_when_nothing_imported(
    federation_sync_service: FederationSyncService,
):
    """Sync is FAILED when total_discovered > 0 but importedTotal == 0."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=2, discoveredAgents=1),
    )
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(
            errors=3,
            errorMessages=["err1", "err2", "err3"],
        ),
    )
    vector_outcome = VectorSyncOutcome()

    mock_stats = SimpleNamespace(importedTotal=0, unimportedTotal=3)
    federation_sync_service._build_federation_stats = AsyncMock(return_value=mock_stats)
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()

    await federation_sync_service._finalize_sync_status(federation, job, mutation_result, vector_outcome)

    federation_sync_service.federation_crud_service.mark_sync_failed.assert_awaited_once()
    federation_sync_service.federation_job_service.mark_failed.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_success.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_marks_success_when_zero_discovered(
    federation_sync_service: FederationSyncService,
):
    """When nothing was discovered, sync succeeds (0 discovered → not failed)."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=0, discoveredAgents=0),
    )
    mutation_result = FederationSyncMutationResult(summary=FederationApplySummary())
    vector_outcome = VectorSyncOutcome()

    mock_stats = SimpleNamespace(importedTotal=0, unimportedTotal=0)
    federation_sync_service._build_federation_stats = AsyncMock(return_value=mock_stats)
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    await federation_sync_service._finalize_sync_status(federation, job, mutation_result, vector_outcome)

    federation_sync_service.federation_crud_service.mark_sync_success.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_federation_stats_residual_formula(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """unimportedTotal = (mcpServerCount + agentCount) - importedTotal by construction."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})

    monkeypatch.setattr(
        "registry.services.federation_sync_service.ExtendedMCPServer.find",
        lambda *a, **kw: _FakeQuery([SimpleNamespace(numTools=3), SimpleNamespace(numTools=2)]),
    )

    discovery_summary = SimpleNamespace(discoveredMcpServers=5, discoveredAgents=3)
    apply_summary = FederationApplySummary(
        createdMcpServers=2,
        updatedMcpServers=1,
        unchangedMcpServers=1,
        vectorSyncFailedMcpServers=1,
        createdAgents=1,
        updatedAgents=1,
        unchangedAgents=0,
        vectorSyncFailedAgents=0,
    )

    stats = await federation_sync_service._build_federation_stats(
        federation.id, discovery_summary, apply_summary, session=None
    )

    assert stats.mcpServerCount == 5
    assert stats.agentCount == 3
    imported = (2 + 1 + 1 - 1) + (1 + 1 + 0 - 0)
    assert stats.importedTotal == imported
    assert stats.unimportedTotal == (5 + 3) - imported
    assert stats.toolCount == 5


@pytest.mark.asyncio
async def test_sync_vector_index_classifies_changed_vs_repair_failures(
    federation_sync_service: FederationSyncService,
):
    """Changed-ARN failures go to failed_changed_*; repair-only to failed_repair_only."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(createdMcpServers=1),
        changed_mcp_runtime_arns={"arn:mcp:changed"},
    )

    federation_sync_service._current_mcp_runtime_arns = AsyncMock(return_value=["arn:mcp:repair"])
    federation_sync_service._current_a2a_runtime_arns = AsyncMock(return_value=[])
    federation_sync_service.mcp_server_repo.has_runtime_identity.return_value = False
    federation_sync_service._sync_mcp_vectors_for_runtime = AsyncMock(side_effect=RuntimeError("fail"))
    federation_sync_service._sync_a2a_vectors_for_runtime = AsyncMock()

    outcome = await federation_sync_service._sync_vector_index_after_commit(
        federation=federation, job=job, mutation_result=mutation_result
    )

    assert "arn:mcp:changed" in outcome.failed_changed_mcp_runtime_arns
    assert "arn:mcp:repair" in outcome.failed_repair_only_runtime_arns
    assert len(outcome.error_messages) == 2


def test_build_last_sync_includes_vector_sync_failed_counts():
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=3, discoveredAgents=2),
    )
    summary = FederationApplySummary(
        createdMcpServers=2,
        createdAgents=1,
        vectorSyncFailedMcpServers=1,
        vectorSyncFailedAgents=1,
    )

    last_sync = FederationSyncService._build_last_sync(job, summary)

    assert last_sync.summary.vectorSyncFailedMcpServers == 1
    assert last_sync.summary.vectorSyncFailedAgents == 1


# ---------------------------------------------------------------------------
# T10 – imported_total nets mongo apply failures
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_imported_total_nets_apply_failures(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})

    monkeypatch.setattr(
        "registry.services.federation_sync_service.ExtendedMCPServer.find",
        lambda *a, **kw: _FakeQuery([SimpleNamespace(numTools=1)]),
    )

    discovery_summary = SimpleNamespace(discoveredMcpServers=3, discoveredAgents=2)
    apply_summary = FederationApplySummary(
        createdMcpServers=2,
        updatedMcpServers=1,
        unchangedMcpServers=0,
        mongoApplyFailedMcpServers=1,
        createdAgents=1,
        updatedAgents=1,
        unchangedAgents=0,
        mongoApplyFailedAgents=1,
    )

    stats = await federation_sync_service._build_federation_stats(
        federation.id,
        discovery_summary,
        apply_summary,
        session=None,
    )

    expected_imported = (2 + 1 + 0 - 0 - 1) + (1 + 1 + 0 - 0 - 1)
    assert stats.importedTotal == expected_imported
    assert stats.unimportedTotal == (3 + 2) - expected_imported


# ---------------------------------------------------------------------------
# T11 – finalize partial success (some failures, importedTotal > 0 → SUCCESS)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_partial_success(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=10, discoveredAgents=0),
    )
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(
            createdMcpServers=10,
            mongoApplyFailedMcpServers=1,
            errors=1,
            errorMessages=["MCP server create failed (remote_id=arn:bad): write conflict"],
        ),
    )
    vector_outcome = VectorSyncOutcome()

    mock_stats = SimpleNamespace(importedTotal=9, unimportedTotal=1)
    federation_sync_service._build_federation_stats = AsyncMock(return_value=mock_stats)
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    await federation_sync_service._finalize_sync_status(federation, job, mutation_result, vector_outcome)

    federation_sync_service.federation_crud_service.mark_sync_success.assert_awaited_once()
    federation_sync_service.federation_job_service.mark_success.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()
    success_call = federation_sync_service.federation_job_service.mark_success.await_args
    assert success_call.kwargs.get("message") is not None


# ---------------------------------------------------------------------------
# T12 – finalize total failure (importedTotal == 0 → FAILED)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_total_failure(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=3, discoveredAgents=0),
    )
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(
            createdMcpServers=3,
            mongoApplyFailedMcpServers=3,
            errors=3,
            errorMessages=[
                "MCP server create failed (remote_id=arn:1): err",
                "MCP server create failed (remote_id=arn:2): err",
                "MCP server create failed (remote_id=arn:3): err",
            ],
        ),
    )
    vector_outcome = VectorSyncOutcome()

    mock_stats = SimpleNamespace(importedTotal=0, unimportedTotal=3)
    federation_sync_service._build_federation_stats = AsyncMock(return_value=mock_stats)
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()

    await federation_sync_service._finalize_sync_status(federation, job, mutation_result, vector_outcome)

    federation_sync_service.federation_crud_service.mark_sync_failed.assert_awaited_once()
    federation_sync_service.federation_job_service.mark_failed.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_success.assert_not_awaited()


# ---------------------------------------------------------------------------
# T14 – delete failure does not affect imported_total
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_failure_does_not_affect_imported_total(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})

    monkeypatch.setattr(
        "registry.services.federation_sync_service.ExtendedMCPServer.find",
        lambda *a, **kw: _FakeQuery([]),
    )

    discovery_summary = SimpleNamespace(discoveredMcpServers=2, discoveredAgents=0)
    apply_summary = FederationApplySummary(
        createdMcpServers=2,
        deletedMcpServers=1,
        errors=1,
        errorMessages=["MCP server delete failed (runtime_arn=arn:del-bad): delete error"],
    )

    stats = await federation_sync_service._build_federation_stats(
        federation.id,
        discovery_summary,
        apply_summary,
        session=None,
    )

    assert stats.importedTotal == 2
    assert stats.unimportedTotal == 0
