from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation.federation_handlers import AwsAgentCoreSyncHandler, AzureAiFoundrySyncHandler
from registry.services.federation_sync_service import FederationSyncMutationResult, FederationSyncService
from registry_pkgs.models.enums import FederationProviderType, FederationStatus, FederationSyncStatus
from registry_pkgs.models.federation_sync_job import FederationApplySummary


@pytest.fixture
def federation_sync_service():
    return FederationSyncService(
        federation_crud_service=MagicMock(),
        federation_job_service=MagicMock(),
        mcp_server_repo=MagicMock(),
        a2a_agent_repo=MagicMock(),
        acl_service=MagicMock(),
        user_service=MagicMock(),
    )


def _make_federation(provider_type: FederationProviderType, provider_config: dict):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=PydanticObjectId(),
        providerType=provider_type,
        providerConfig=provider_config,
        status=FederationStatus.ACTIVE,
        syncStatus=FederationSyncStatus.IDLE,
        version=1,
        createdAt=now,
        updatedAt=now,
    )


@pytest.mark.asyncio
async def test_discover_entities_dispatches_to_aws_handler(federation_sync_service: FederationSyncService):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    expected = {"mcp_servers": [], "a2a_agents": [], "skipped_runtimes": []}

    aws_handler = MagicMock(spec=AwsAgentCoreSyncHandler)
    aws_handler.discover_entities = AsyncMock(return_value=expected)
    federation_sync_service.sync_handlers[FederationProviderType.AWS_AGENTCORE] = aws_handler

    result = await federation_sync_service._discover_entities(federation)

    aws_handler.discover_entities.assert_awaited_once_with(federation)
    assert result == expected


@pytest.mark.asyncio
async def test_aws_handler_passes_resource_tags_filter_to_client():
    fake_discovery_client = MagicMock()
    fake_runtime_invoker = MagicMock()
    fake_runtime_invoker.enrich_mcp_server = AsyncMock()
    fake_runtime_invoker.enrich_a2a_agent = AsyncMock()
    handler = AwsAgentCoreSyncHandler(
        discovery_client=fake_discovery_client,
        runtime_invoker=fake_runtime_invoker,
    )
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole",
            "resourceTagsFilter": {"env": "production", "team": "platform"},
        },
    )
    fake_discovery_client.discover_runtime_entities = AsyncMock(
        return_value={"mcp_servers": [], "a2a_agents": [], "skipped_runtimes": []}
    )

    result = await handler.discover_entities(federation)

    fake_discovery_client.discover_runtime_entities.assert_awaited_once_with(
        author_id=None,
        region="us-east-1",
        assume_role_arn="arn:aws:iam::123456789012:role/TestRole",
        resource_tags_filter={"env": "production", "team": "platform"},
    )
    assert result == {"mcp_servers": [], "a2a_agents": [], "skipped_runtimes": []}


@pytest.mark.asyncio
async def test_azure_handler_is_registered_and_returns_clear_not_implemented_error(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(
        FederationProviderType.AZURE_AI_FOUNDRY,
        {
            "region": "eastus",
            "tenantId": "tenant-1",
            "subscriptionId": "sub-1",
            "resourceGroup": "rg-1",
            "workspaceName": "ws-1",
        },
    )

    handler = federation_sync_service.get_sync_handler(FederationProviderType.AZURE_AI_FOUNDRY)

    assert isinstance(handler, AzureAiFoundrySyncHandler)

    with pytest.raises(ValueError, match="azure_ai_foundry is not implemented yet"):
        await federation_sync_service._discover_entities(federation)


@pytest.mark.asyncio
async def test_run_delete_restores_active_status_when_delete_fails(federation_sync_service: FederationSyncService):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    federation.status = FederationStatus.DELETING
    federation.syncStatus = FederationSyncStatus.SYNCING
    job = SimpleNamespace(id=PydanticObjectId(), jobType="delete_sync", startedAt=datetime.now(UTC))

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_delete_failed = AsyncMock()
    federation_sync_service._delete_transaction = AsyncMock(side_effect=RuntimeError("delete failed"))

    with pytest.raises(RuntimeError, match="delete failed"):
        await federation_sync_service.run_delete(federation=federation, job=job)

    federation_sync_service.federation_crud_service.mark_delete_failed.assert_awaited_once_with(
        federation, "delete failed"
    )
    federation_sync_service.federation_job_service.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_federation_and_create_resync_job_creates_pending_job(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    updated = SimpleNamespace(
        **{
            **federation.__dict__,
            "providerConfig": {"region": "us-west-2", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
            "version": 2,
        }
    )
    job = SimpleNamespace(id=PydanticObjectId(), jobType="config_resync", createdAt=datetime.now(UTC))

    federation_sync_service.federation_crud_service.update_federation = AsyncMock(return_value=updated)
    federation_sync_service.federation_job_service.create_job = AsyncMock(return_value=job)
    federation_sync_service.federation_crud_service.mark_sync_pending = AsyncMock(return_value=updated)

    result, created_job = await FederationSyncService.update_federation_and_create_resync_job.__wrapped__(
        federation_sync_service,
        federation=federation,
        display_name="Updated",
        description="Updated",
        tags=["prod"],
        normalized_provider_config={"region": "us-west-2", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
        version=federation.version,
        updated_by="user-1",
    )

    federation_sync_service.federation_crud_service.update_federation.assert_awaited_once()
    federation_sync_service.federation_job_service.create_job.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_pending.assert_awaited_once()
    assert federation_sync_service.federation_crud_service.mark_sync_pending.await_args.args[0] == updated
    assert federation_sync_service.federation_crud_service.mark_sync_pending.await_args.kwargs["last_sync"].status == (
        FederationSyncStatus.PENDING
    )
    assert result == updated
    assert created_job == job


@pytest.mark.asyncio
async def test_run_sync_calls_vector_sync_after_commit(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId(), startedAt=datetime.now(UTC))
    mutation_result = FederationSyncMutationResult(summary=FederationApplySummary())

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_sync_transaction = AsyncMock(return_value=mutation_result)
    federation_sync_service._sync_vector_index_after_commit = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    result = await federation_sync_service.run_sync(federation=federation, job=job, user_id="user-1")

    assert result == job
    federation_sync_service._sync_vector_index_after_commit.assert_awaited_once_with(
        federation=federation,
        job=job,
        mutation_result=mutation_result,
    )
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_failed.assert_not_awaited()


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
        await federation_sync_service.run_sync(federation=federation, job=job, user_id="user-1")

    federation_sync_service.federation_crud_service.mark_sync_failed.assert_awaited_once()
    failed_last_sync = federation_sync_service.federation_crud_service.mark_sync_failed.await_args.kwargs["last_sync"]
    assert failed_last_sync.status == FederationSyncStatus.FAILED
    assert failed_last_sync.summary is not None
    assert failed_last_sync.summary.errorMessages == ["discovery failed"]


@pytest.mark.asyncio
async def test_sync_vector_index_after_commit_logs_and_continues_on_vector_failure(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(createdMcpServers=1, deletedAgents=1),
        changed_mcp_runtime_arns={"arn:mcp:1"},
        changed_a2a_runtime_arns={"arn:a2a:1"},
    )

    federation_sync_service._sync_mcp_vectors_for_runtime = AsyncMock(side_effect=RuntimeError("vector down"))
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

    with caplog.at_level("INFO"):
        await federation_sync_service._sync_vector_index_after_commit(
            federation=federation,
            job=job,
            mutation_result=mutation_result,
        )

    assert "Federation vector sync plan" in caplog.text
    assert "mcp_rebuild=0" in caplog.text
    assert "a2a_rebuild=0" in caplog.text
    assert "Federation vector sync completed" in caplog.text


class _FakeQuery:
    def __init__(self, items):
        self._items = items

    async def to_list(self):
        return list(self._items)


@pytest.mark.asyncio
async def test_apply_sync_mutations_skips_a2a_insert_when_path_belongs_to_another_resource(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    conflicting_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/hosted-agent-257ko",
        federationRefId=PydanticObjectId(),
        federationMetadata={"runtimeArn": "arn:existing"},
    )
    discovered_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/hosted-agent-257ko",
        card=SimpleNamespace(name="hosted_agent_257ko"),
        tags=[],
        status="active",
        isEnabled=True,
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "path" in query:
            return _FakeQuery([conflicting_agent])
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServerDocument.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._apply_sync_mutations(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_agent],
    )

    assert result.summary.skippedAgents == 1
    assert result.summary.createdAgents == 0
    discovered_agent.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_sync_transaction_marks_federation_failed_when_resource_enrichment_fails(
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
    mutation_result = FederationSyncMutationResult(summary=summary)

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_crud_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.update_discovery_summary = AsyncMock()
    federation_sync_service._apply_sync_mutations = AsyncMock(return_value=mutation_result)
    federation_sync_service.federation_job_service.update_apply_summary = AsyncMock()
    federation_sync_service._build_federation_stats = AsyncMock(return_value=SimpleNamespace())
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()

    result = await FederationSyncService._commit_sync_transaction.__wrapped__(
        federation_sync_service,
        federation=federation,
        job=job,
        discovered={"mcp_servers": [SimpleNamespace()], "a2a_agents": [SimpleNamespace()]},
    )

    assert result == mutation_result
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_success.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_failed.assert_awaited_once()
    federation_sync_service.federation_job_service.mark_success.assert_not_awaited()
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


def test_build_last_sync_carries_error_count_and_failed_status():
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

    assert last_sync.status == FederationSyncStatus.FAILED
    assert last_sync.summary.errors == 1
    assert last_sync.summary.errorMessages == ["A2A agent pharmacy_fraud_a2a: boom"]
