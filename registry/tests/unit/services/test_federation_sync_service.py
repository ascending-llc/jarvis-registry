import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation.federation_handlers import AwsAgentCoreSyncHandler
from registry.services.federation_sync_service import (
    ACL_INHERITANCE_BATCH_SIZE,
    FederationSyncMutationResult,
    FederationSyncPlan,
    FederationSyncService,
)
from registry_pkgs.models import A2AAgent, ExtendedMCPServer, PrincipalType, ResourceType
from registry_pkgs.models.enums import FederationProviderType, FederationStatus, FederationSyncStatus, RoleBits
from registry_pkgs.models.extended_acl_entry import ExtendedAclEntry, ExtendedResourceType
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
        createdAt=now,
        updatedAt=now,
    )


class _FakeQuery:
    def __init__(self, items):
        self._items = items

    async def to_list(self):
        return list(self._items)


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
async def test_azure_sync_is_not_implemented(federation_sync_service: FederationSyncService):
    federation = _make_federation(
        FederationProviderType.AZURE_AI_FOUNDRY,
        {"projectEndpoint": "https://example.projects.ai.azure.com"},
    )

    with pytest.raises(ValueError, match="not implemented yet"):
        await federation_sync_service._discover_entities(federation)


@pytest.mark.asyncio
async def test_run_delete_marks_job_success_and_cleans_up_vectors(federation_sync_service: FederationSyncService):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    federation.status = FederationStatus.DELETING
    job = SimpleNamespace(id=PydanticObjectId(), jobType="delete_sync", startedAt=datetime.now(UTC))

    mcp_arns = ["arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp-1"]
    a2a_arns = ["arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a-1"]

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service._delete_transaction = AsyncMock(return_value=(mcp_arns, a2a_arns))
    federation_sync_service._delete_vectors_for_federation = AsyncMock(return_value=[])

    result = await federation_sync_service.run_delete(federation=federation, job=job)

    federation_sync_service._delete_transaction.assert_awaited_once_with(federation, current_job_id=job.id)
    federation_sync_service._delete_vectors_for_federation.assert_awaited_once_with(
        str(federation.id), mcp_arns, a2a_arns
    )
    federation_sync_service.federation_job_service.mark_success.assert_awaited_once_with(job)
    assert result is job


@pytest.mark.asyncio
async def test_run_delete_records_vector_errors_in_job_but_still_succeeds(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    federation.status = FederationStatus.DELETING
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="delete_sync",
        startedAt=datetime.now(UTC),
        applySummary=FederationApplySummary(),
    )

    vector_errors = ["mcp vector cleanup failed for arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp-1"]

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service._delete_transaction = AsyncMock(return_value=(["arn:..."], []))
    federation_sync_service._delete_vectors_for_federation = AsyncMock(return_value=vector_errors)

    result = await federation_sync_service.run_delete(federation=federation, job=job)

    assert result.applySummary.errorMessages == vector_errors
    federation_sync_service.federation_job_service.mark_success.assert_awaited_once_with(job)


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
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(),
        stats=SimpleNamespace(),
        last_sync=SimpleNamespace(),
    )

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_sync_transaction = AsyncMock(return_value=mutation_result)
    federation_sync_service._sync_vector_index_after_commit = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()

    result = await federation_sync_service.run_sync(federation=federation, job=job, user_id="user-1")

    assert result == job
    federation_sync_service._sync_vector_index_after_commit.assert_awaited_once_with(
        federation=federation,
        job=job,
        mutation_result=mutation_result,
    )
    federation_sync_service.federation_crud_service.mark_sync_success.assert_awaited_once_with(
        federation,
        mutation_result.last_sync,
        mutation_result.stats,
    )
    federation_sync_service.federation_job_service.mark_success.assert_awaited_once_with(job)
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_sync_marks_failed_when_vector_sync_fails(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=None,
    )
    mutation_result = FederationSyncMutationResult(summary=FederationApplySummary())

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_sync_transaction = AsyncMock(return_value=mutation_result)
    federation_sync_service._sync_vector_index_after_commit = AsyncMock(side_effect=RuntimeError("vector down"))
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    with pytest.raises(RuntimeError, match="vector down"):
        await federation_sync_service.run_sync(federation=federation, job=job, user_id="user-1")

    federation_sync_service.federation_crud_service.mark_sync_failed.assert_awaited_once()
    federation_sync_service.federation_job_service.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_manual_sync_does_not_mutate_or_create_jobs(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    summary = FederationApplySummary(createdMcpServers=1)
    sync_plan = SimpleNamespace(summary=summary, discovered_mcp_count=1, discovered_a2a_count=0)

    federation_sync_service._discover_entities = AsyncMock(
        return_value={"mcp_servers": [SimpleNamespace()], "a2a_agents": []}
    )
    federation_sync_service._build_sync_plan = AsyncMock(return_value=sync_plan)
    federation_sync_service.federation_job_service.create_job = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_pending = AsyncMock()
    federation_sync_service.federation_crud_service.mark_syncing = AsyncMock()
    federation_sync_service._sync_vector_index_after_commit = AsyncMock()

    result = await federation_sync_service.preview_manual_sync(
        federation=federation,
        reason="test",
        triggered_by="user-1",
    )

    assert result.provider_type == federation.providerType
    assert result.discovered_mcp_count == 1
    assert result.summary.createdMcpServers == 1
    federation_sync_service.federation_job_service.create_job.assert_not_awaited()
    federation_sync_service.federation_crud_service.mark_sync_pending.assert_not_awaited()
    federation_sync_service.federation_crud_service.mark_syncing.assert_not_awaited()
    federation_sync_service._sync_vector_index_after_commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_sync_plan_handles_runtime_type_switch_without_discovery_mutation(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1"

    existing_mcp = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="runtime-r1",
        path="/agentcore/mcp/runtime-r1",
        config={"runtimeAccess": {"mode": "iam"}},
        status="active",
        numTools=1,
        tags=[],
    )
    discovered_a2a = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "2"},
        path="/agentcore/a2a/runtime-r1",
        config=SimpleNamespace(runtimeAccess=SimpleNamespace(mode="jwt")),
        card=SimpleNamespace(name="runtime-r1"),
    )

    def _fake_mcp_find(query, session=None):
        assert query == {"federationRefId": federation.id}
        assert session is None
        return _FakeQuery([existing_mcp])

    def _fake_a2a_find(query, session=None):
        assert session is None
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([])
        if query == {"path": {"$in": ["/agentcore/a2a/runtime-r1"]}}:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr(ExtendedMCPServer, "find", _fake_mcp_find)
    monkeypatch.setattr(A2AAgent, "find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_a2a],
    )

    assert sync_plan.summary.createdAgents == 1
    assert sync_plan.summary.deletedMcpServers == 1
    assert sync_plan.summary.deletedAgents == 0
    assert len(sync_plan.a2a_creates) == 1
    assert len(sync_plan.mcp_deletes) == 1
    assert sync_plan.mcp_deletes[0][1] == runtime_arn


@pytest.mark.asyncio
async def test_run_sync_returns_failed_job_without_vector_when_apply_summary_has_errors(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId(), startedAt=datetime.now(UTC))
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(errors=1, errorMessages=["boom"]),
        stats=SimpleNamespace(),
        last_sync=SimpleNamespace(),
    )

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_sync_transaction = AsyncMock(return_value=mutation_result)
    federation_sync_service._sync_vector_index_after_commit = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_success = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()

    result = await federation_sync_service.run_sync(federation=federation, job=job, user_id="user-1")

    assert result == job
    federation_sync_service._sync_vector_index_after_commit.assert_not_awaited()
    federation_sync_service.federation_crud_service.mark_sync_success.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_success.assert_not_awaited()


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
async def test_sync_vector_index_after_commit_raises_on_vector_failure(
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

    with pytest.raises(RuntimeError, match="mcp runtime rebuild failed"):
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


class _FakeQuery:
    def __init__(self, items):
        self._items = items

    async def to_list(self):
        return list(self._items)


@pytest.mark.asyncio
async def test_build_sync_plan_skips_a2a_insert_when_path_belongs_to_another_resource(
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

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_agent],
    )

    assert result.summary.skippedAgents == 1
    assert result.summary.createdAgents == 0
    assert result.summary.errors == 0
    assert result.summary.errorMessages == []
    discovered_agent.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_sync_plan_skips_mcp_insert_without_marking_error(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    conflicting_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="shared-server",
        federationRefId=PydanticObjectId(),
        federationMetadata={"runtimeArn": "arn:existing"},
    )
    discovered_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="shared-server",
        tags=[],
        status="active",
        isEnabled=True,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "serverName" in query:
            return _FakeQuery([conflicting_server])
        raise AssertionError(f"unexpected query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_server],
        discovered_a2a=[],
    )

    assert result.summary.skippedMcpServers == 1
    assert result.summary.createdMcpServers == 0
    assert result.summary.errors == 0
    assert result.summary.errorMessages == []
    discovered_server.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_sync_plan_does_not_treat_planned_a2a_create_as_persisted_path_owner(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    existing_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/existing-path",
        card=SimpleNamespace(name="existing"),
        tags=[],
        status="active",
        isEnabled=True,
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "1"},
    )
    discovered_new_agent = SimpleNamespace(
        id=None,
        path="/agentcore/a2a/target-path",
        card=SimpleNamespace(name="new-agent"),
        tags=[],
        status="active",
        isEnabled=True,
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )
    discovered_existing_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/target-path",
        card=SimpleNamespace(name="existing"),
        tags=[],
        status="active",
        isEnabled=True,
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "2"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_agent])
        if "path" in query:
            return _FakeQuery([])
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_new_agent, discovered_existing_agent],
    )

    assert result.summary.createdAgents == 1
    assert result.summary.updatedAgents == 1
    assert result.summary.skippedAgents == 0
    assert result.a2a_creates == [(discovered_new_agent, "arn:new")]
    assert result.a2a_updates == [(existing_agent, discovered_existing_agent, "arn:existing")]


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
    federation_sync_service._build_sync_plan = AsyncMock(
        return_value=SimpleNamespace(
            summary=summary,
            discovered_mcp_count=1,
            discovered_a2a_count=1,
        )
    )
    federation_sync_service._apply_sync_plan = AsyncMock(return_value=mutation_result)
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
        user_id="user-1",
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


# ---------------------------------------------------------------------------
# ACL ownership grant tests
# ---------------------------------------------------------------------------


def _make_sync_plan(
    federation_id, provider_type, mcp_creates=None, mcp_updates=None, a2a_creates=None, a2a_updates=None
):
    from registry.services.federation_sync_service import FederationSyncPlan

    mcp_creates = mcp_creates or []
    mcp_updates = mcp_updates or []
    a2a_creates = a2a_creates or []
    a2a_updates = a2a_updates or []

    return FederationSyncPlan(
        federation_id=federation_id,
        provider_type=provider_type,
        summary=FederationApplySummary(),
        discovered_mcp_count=len(mcp_creates) + len(mcp_updates),
        discovered_a2a_count=len(a2a_creates) + len(a2a_updates),
        mcp_creates=mcp_creates,
        mcp_updates=mcp_updates,
        mcp_deletes=[],
        a2a_creates=a2a_creates,
        a2a_updates=a2a_updates,
        a2a_deletes=[],
    )


# ==================== ACL Inheritance Tests ====================


def _make_acl_entry(
    principal_type: str,
    principal_id: str,
    resource_type: str,
    resource_id: PydanticObjectId,
    perm_bits: int,
    role_id: PydanticObjectId | None = None,
):
    """Helper to create a mock ACL entry for testing."""
    now = datetime.now(UTC)
    entry = MagicMock(spec=ExtendedAclEntry)
    entry.id = PydanticObjectId()
    entry.principalType = principal_type
    entry.principalId = principal_id
    entry.resourceType = resource_type
    entry.resourceId = resource_id
    entry.roleId = role_id
    entry.permBits = perm_bits
    entry.grantedAt = now
    entry.createdAt = now
    entry.updatedAt = now
    return entry


@pytest.mark.asyncio
async def test_build_sync_plan_tracks_unchanged_resources_for_acl_inheritance(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    mcp_id = PydanticObjectId()
    a2a_id = PydanticObjectId()
    mcp_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/unchanged-mcp"
    a2a_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/unchanged-a2a"

    existing_mcp = SimpleNamespace(
        id=mcp_id,
        serverName="unchanged-mcp",
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": mcp_runtime_arn, "runtimeVersion": "1"},
    )
    discovered_mcp = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="unchanged-mcp",
        federationMetadata={"runtimeArn": mcp_runtime_arn, "runtimeVersion": "1"},
    )
    existing_a2a = SimpleNamespace(
        id=a2a_id,
        path="/agentcore/a2a/unchanged-a2a",
        config=SimpleNamespace(type="jsonrpc"),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": a2a_runtime_arn, "runtimeVersion": "1"},
    )
    discovered_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/unchanged-a2a",
        card=SimpleNamespace(name="unchanged-a2a"),
        config=SimpleNamespace(type="jsonrpc"),
        federationMetadata={"runtimeArn": a2a_runtime_arn, "runtimeVersion": "1"},
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    def _fake_mcp_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([existing_mcp])
        raise AssertionError(f"unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([existing_a2a])
        if query == {"path": {"$in": ["/agentcore/a2a/unchanged-a2a"]}}:
            return _FakeQuery([existing_a2a])
        raise AssertionError(f"unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_mcp],
        discovered_a2a=[discovered_a2a],
    )

    assert sync_plan.summary.unchangedMcpServers == 1
    assert sync_plan.summary.unchangedAgents == 1
    assert sync_plan.mcp_pre_existing_acl_targets == [mcp_id]
    assert sync_plan.a2a_pre_existing_acl_targets == [a2a_id]


@pytest.mark.asyncio
async def test_apply_sync_plan_inherits_acl_to_unchanged_mcp_and_a2a_resources(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    mcp_id = PydanticObjectId()
    a2a_id = PydanticObjectId()
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER)
    ]
    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(unchangedMcpServers=1, unchangedAgents=1),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=1,
        discovered_a2a_count=1,
        mcp_pre_existing_acl_targets=[mcp_id],
        a2a_pre_existing_acl_targets=[a2a_id],
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=(federation_acl_entries, True))
    federation_sync_service._batch_inherit_federation_acl = AsyncMock()

    await federation_sync_service._apply_sync_plan(sync_plan)

    federation_sync_service._batch_inherit_federation_acl.assert_awaited_once_with(
        federation_acl_entries=federation_acl_entries,
        resources=[
            (ResourceType.MCPSERVER, mcp_id),
            (ResourceType.REMOTE_AGENT, a2a_id),
        ],
    )


@pytest.mark.asyncio
async def test_apply_sync_plan_inherits_acl_to_created_updated_and_unchanged_resources(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """
    Test that ACL inheritance applies to:
    - Pre-existing unchanged resources
    - Newly created resources (after insert)
    - Updated resources (after save)
    But NOT to deleted resources.
    """
    federation_id = PydanticObjectId()
    unchanged_mcp_id = PydanticObjectId()
    unchanged_a2a_id = PydanticObjectId()

    # Mock resources for creates - these will have IDs set after insert
    new_mcp_id = PydanticObjectId()
    new_a2a_id = PydanticObjectId()

    # Mock resources for updates
    existing_mcp = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="existing-mcp",
        path="/path",
        tags=[],
        config={},
        status="active",
        numTools=1,
        federationMetadata={},
        save=AsyncMock(),
    )
    existing_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        path="/path",
        card=SimpleNamespace(name="existing"),
        tags=[],
        status="active",
        isEnabled=True,
        wellKnown=None,
        config=SimpleNamespace(type="jsonrpc"),
        federationMetadata={},
        save=AsyncMock(),
    )

    # Mock resources for deletes
    deleted_mcp = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock())
    deleted_a2a = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock())

    # Mock new items for creates/updates
    new_mcp_item = SimpleNamespace(
        serverName="new-mcp",
        path="/new-path",
        tags=[],
        config={},
        status="active",
        numTools=1,
        federationMetadata={},
    )
    new_a2a_item = SimpleNamespace(
        path="/new-path",
        card=SimpleNamespace(name="new"),
        tags=[],
        status="active",
        isEnabled=True,
        wellKnown=None,
        config=SimpleNamespace(type="jsonrpc"),
        federationMetadata={},
    )

    # Mock insert to set IDs on the items
    async def mock_mcp_insert(**kwargs):
        new_mcp_item.id = new_mcp_id

    async def mock_a2a_insert(**kwargs):
        new_a2a_item.id = new_a2a_id

    new_mcp_item.insert = AsyncMock(side_effect=mock_mcp_insert)
    new_a2a_item.insert = AsyncMock(side_effect=mock_a2a_insert)

    update_mcp_item = SimpleNamespace(
        serverName="updated-mcp",
        path="/updated-path",
        tags=[],
        config={},
        status="active",
        numTools=2,
        federationMetadata={},
    )
    update_a2a_item = SimpleNamespace(
        path="/updated-path",
        card=SimpleNamespace(name="updated"),
        tags=[],
        status="active",
        isEnabled=True,
        wellKnown=None,
        config=SimpleNamespace(type="jsonrpc"),
        federationMetadata={},
    )

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER)
    ]

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(
            unchangedMcpServers=1,
            unchangedAgents=1,
            createdMcpServers=1,
            createdAgents=1,
            updatedMcpServers=1,
            updatedAgents=1,
            deletedMcpServers=1,
            deletedAgents=1,
        ),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=3,
        discovered_a2a_count=3,
        mcp_pre_existing_acl_targets=[unchanged_mcp_id],
        a2a_pre_existing_acl_targets=[unchanged_a2a_id],
        mcp_creates=[(new_mcp_item, "arn:new-mcp")],
        a2a_creates=[(new_a2a_item, "arn:new-a2a")],
        mcp_updates=[(existing_mcp, update_mcp_item, "arn:updated-mcp")],
        a2a_updates=[(existing_a2a, update_a2a_item, "arn:updated-a2a")],
        mcp_deletes=[(deleted_mcp, "arn:deleted-mcp")],
        a2a_deletes=[(deleted_a2a, "arn:deleted-a2a")],
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=(federation_acl_entries, True))
    federation_sync_service._batch_inherit_federation_acl = AsyncMock()

    await federation_sync_service._apply_sync_plan(sync_plan)

    # Verify that _batch_inherit_federation_acl was called
    federation_sync_service._batch_inherit_federation_acl.assert_awaited_once()

    # Get the resources argument from the call
    call_args = federation_sync_service._batch_inherit_federation_acl.await_args
    resources = call_args.kwargs["resources"]

    # Extract resource IDs from the resources list
    resource_ids = {str(resource_id) for _, resource_id in resources}

    # Should include: unchanged (2), created (2), updated (2) = 6 total
    assert len(resources) == 6

    # Should include unchanged resources
    assert str(unchanged_mcp_id) in resource_ids
    assert str(unchanged_a2a_id) in resource_ids

    # Should include created resources (after insert sets their IDs)
    assert str(new_mcp_id) in resource_ids
    assert str(new_a2a_id) in resource_ids

    # Should include updated resources
    assert str(existing_mcp.id) in resource_ids
    assert str(existing_a2a.id) in resource_ids

    # Should NOT include deleted resources
    assert str(deleted_mcp.id) not in resource_ids
    assert str(deleted_a2a.id) not in resource_ids


@pytest.mark.asyncio
async def test_apply_sync_plan_raises_when_federation_acl_query_fails(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(),
        federation_id=PydanticObjectId(),
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=0,
        discovered_a2a_count=0,
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], False))
    federation_sync_service._batch_inherit_federation_acl = AsyncMock()

    with pytest.raises(RuntimeError, match="could not query federation ACL"):
        await federation_sync_service._apply_sync_plan(sync_plan)

    federation_sync_service._batch_inherit_federation_acl.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_federation_acl_entries_uses_current_transaction_session(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    session = object()
    find_calls = []

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: session)

    def mock_find(query, **kwargs):
        find_calls.append({"query": query, "kwargs": kwargs})
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry.find", mock_find)

    entries, query_success = await federation_sync_service._get_federation_acl_entries(federation_id)

    assert entries == []
    assert query_success is True
    assert find_calls == [
        {
            "query": {
                "resourceType": ExtendedResourceType.FEDERATION,
                "resourceId": federation_id,
                "principalType": {"$ne": PrincipalType.PUBLIC.value},
                "principalId": {"$ne": None},
            },
            "kwargs": {"session": session},
        }
    ]


@pytest.mark.asyncio
async def test_batch_inherit_acl_normalizes_resource_type_before_existence_check(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER)
    ]
    existing_resource_acl = [
        _make_acl_entry(
            PrincipalType.USER,
            "kent",
            ExtendedResourceType.MCPSERVER,
            server_id,
            RoleBits.EDITOR,
        )
    ]
    inserted_entries = []

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    class MockExtendedAclEntry:
        @staticmethod
        def find(query, **kwargs):
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry", MockExtendedAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    assert inserted_entries == []


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_1_empty_resource(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 1: Federation has ACL, resource is empty → inherit all.

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: empty
    Resource ACL after: Kent=owner, Ryo=editor, Celeste=viewer
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    # Federation ACL entries
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", ExtendedResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", ExtendedResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Mock: Federation has 3 ACL entries, resource has 0
    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockExtendedAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == ExtendedResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery([])  # Resource has no existing ACL
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry", MockExtendedAclEntry)

    # Execute
    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: All 3 ACL entries should be inserted
    assert len(inserted_entries) == 3
    assert {e.principalId for e in inserted_entries} == {"kent", "ryo", "celeste"}
    assert {e.permBits for e in inserted_entries} == {RoleBits.OWNER, RoleBits.EDITOR, RoleBits.VIEWER}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_2_preserve_existing(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 2: Federation has ACL, resource has other users → inherit + preserve.

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: Alex=owner
    Resource ACL after: Kent=owner, Ryo=editor, Celeste=viewer, Alex=owner (preserved)
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", ExtendedResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", ExtendedResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Resource already has Alex as owner
    existing_resource_acl = [
        _make_acl_entry(PrincipalType.USER, "alex", ResourceType.MCPSERVER, server_id, RoleBits.OWNER)
    ]

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockExtendedAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == ExtendedResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry", MockExtendedAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only Kent, Ryo, Celeste should be inserted (Alex is preserved)
    assert len(inserted_entries) == 3
    assert {e.principalId for e in inserted_entries} == {"kent", "ryo", "celeste"}
    assert "alex" not in {e.principalId for e in inserted_entries}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_3_higher_permission_preserved(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 3: User has higher permission on resource → preserve existing.

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: Ryo=owner
    Resource ACL after: Kent=owner, Ryo=owner (preserved, NOT downgraded), Celeste=viewer
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", ExtendedResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", ExtendedResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Ryo already has OWNER on the resource
    existing_resource_acl = [
        _make_acl_entry(PrincipalType.USER, "ryo", ResourceType.MCPSERVER, server_id, RoleBits.OWNER)
    ]

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockExtendedAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == ExtendedResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry", MockExtendedAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only Kent and Celeste should be inserted (Ryo already exists)
    assert len(inserted_entries) == 2
    assert {e.principalId for e in inserted_entries} == {"kent", "celeste"}
    assert "ryo" not in {e.principalId for e in inserted_entries}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_4_lower_permission_preserved(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 4: User has lower permission on resource → preserve existing (no upgrade).

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: Kent=editor
    Resource ACL after: Kent=editor (preserved, NOT upgraded), Ryo=editor, Celeste=viewer
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", ExtendedResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", ExtendedResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Kent already has EDITOR (lower than OWNER in Federation)
    existing_resource_acl = [
        _make_acl_entry(PrincipalType.USER, "kent", ResourceType.MCPSERVER, server_id, RoleBits.EDITOR)
    ]

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockExtendedAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == ExtendedResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry", MockExtendedAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only Ryo and Celeste should be inserted (Kent already exists, NOT upgraded)
    assert len(inserted_entries) == 2
    assert {e.principalId for e in inserted_entries} == {"ryo", "celeste"}
    assert "kent" not in {e.principalId for e in inserted_entries}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_validates_principal_id(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    ACL entries with null principalId should be skipped with a warning.
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    # Create ACL entries, including one with None principalId
    valid_entry = _make_acl_entry(
        PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER
    )

    invalid_entry = _make_acl_entry(
        PrincipalType.USER, "invalid", ExtendedResourceType.FEDERATION, federation_id, RoleBits.EDITOR
    )
    invalid_entry.principalId = None  # Simulate invalid entry

    federation_acl_entries = [valid_entry, invalid_entry]

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockExtendedAclEntry:
        @staticmethod
        def find(query, **kwargs):
            if query.get("resourceType") == ExtendedResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery([])
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry", MockExtendedAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only valid entry should be inserted
    assert len(inserted_entries) == 1
    assert inserted_entries[0].principalId == "kent"


@pytest.mark.asyncio
async def test_batch_inherit_acl_uses_batched_insert(federation_sync_service: FederationSyncService, monkeypatch):
    """
    Large batches of ACL entries should be inserted in chunks (500 per batch).
    Verify that insert_many is called with ordered=False for idempotency.
    """
    federation_id = PydanticObjectId()

    # Create 3 federation ACL entries
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "user1", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "user2", ExtendedResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "user3", ExtendedResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Create 200 resources (will generate 600 ACL entries, split into 2 batches)
    resources = [(ResourceType.MCPSERVER, PydanticObjectId()) for _ in range(200)]

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    insert_calls = []

    # Create a mock class that has both class methods and constructor
    class MockExtendedAclEntry:
        @staticmethod
        def find(query, **kwargs):
            if query.get("resourceType") == ExtendedResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery([])  # No existing ACL
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            insert_calls.append({"count": len(entries), "kwargs": kwargs})

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry", MockExtendedAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=resources,
    )

    # Verify: Should have 2 insert calls (600 entries / batch size = 2 batches)
    assert len(insert_calls) == 2
    assert insert_calls[0]["count"] == ACL_INHERITANCE_BATCH_SIZE
    assert insert_calls[1]["count"] == len(resources) * len(federation_acl_entries) - ACL_INHERITANCE_BATCH_SIZE

    # Verify: All calls use ordered=False
    for call in insert_calls:
        assert call["kwargs"].get("ordered") is False


@pytest.mark.asyncio
async def test_batch_inherit_acl_raises_after_failure(federation_sync_service: FederationSyncService, monkeypatch):
    """ACL inheritance failures should abort the sync instead of reporting false success."""
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", ExtendedResourceType.FEDERATION, federation_id, RoleBits.OWNER),
    ]

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)

    def mock_find(query, **kwargs):
        if query.get("resourceType") == ExtendedResourceType.FEDERATION:
            return _FakeQuery(federation_acl_entries)
        raise Exception("Database connection error")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedAclEntry.find", mock_find)

    with pytest.raises(RuntimeError, match="ACL inheritance failed"):
        await federation_sync_service._batch_inherit_federation_acl(
            federation_acl_entries=federation_acl_entries,
            resources=[(ResourceType.MCPSERVER, server_id)],
        )
