import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation.federation_handlers import AwsAgentCoreSyncHandler
from registry.services.federation_sync_service import FederationSyncMutationResult, FederationSyncService
from registry_pkgs.models import A2AAgent, ExtendedMCPServer, ResourceType
from registry_pkgs.models.enums import FederationProviderType, FederationStatus, FederationSyncStatus, RoleBits
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
    discovered_agent.insert.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_apply_sync_plan_grants_owner_acl_on_mcp_create(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """Creating an MCP server via sync grants the syncing user OWNER ACL."""
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    server = MagicMock()
    server.id = server_id
    server.federationMetadata = {}
    server.insert = AsyncMock()

    plan = _make_sync_plan(
        federation_id,
        FederationProviderType.AWS_AGENTCORE,
        mcp_creates=[(server, "arn:mcp:1")],
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service.acl_service.grant_permission = AsyncMock()

    await federation_sync_service._apply_sync_plan(plan, user_id="user-yulin")

    federation_sync_service.acl_service.grant_permission.assert_awaited_once_with(
        principal_type="user",
        principal_id="user-yulin",
        resource_type=ResourceType.MCPSERVER,
        resource_id=server_id,
        perm_bits=RoleBits.OWNER,
    )


@pytest.mark.asyncio
async def test_apply_sync_plan_grants_owner_acl_on_a2a_create(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """Creating an A2A agent via sync grants the syncing user OWNER ACL."""
    federation_id = PydanticObjectId()
    agent_id = PydanticObjectId()

    agent = MagicMock()
    agent.id = agent_id
    agent.federationMetadata = {}
    agent.insert = AsyncMock()

    plan = _make_sync_plan(
        federation_id,
        FederationProviderType.AWS_AGENTCORE,
        a2a_creates=[(agent, "arn:a2a:1")],
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service.acl_service.grant_permission = AsyncMock()

    await federation_sync_service._apply_sync_plan(plan, user_id="user-yulin")

    federation_sync_service.acl_service.grant_permission.assert_awaited_once_with(
        principal_type="user",
        principal_id="user-yulin",
        resource_type=ResourceType.REMOTE_AGENT,
        resource_id=agent_id,
        perm_bits=RoleBits.OWNER,
    )


@pytest.mark.asyncio
async def test_apply_sync_plan_adds_second_owner_on_mcp_update(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """Updating an existing MCP server adds the new syncer as co-owner (upsert is idempotent)."""
    federation_id = PydanticObjectId()
    existing_id = PydanticObjectId()

    existing = MagicMock()
    existing.id = existing_id
    existing.federationMetadata = {}
    existing.save = AsyncMock()

    item = MagicMock()
    item.serverName = "mcp-1"
    item.path = "/mcp-1"
    item.tags = []
    item.config = {}
    item.status = "active"
    item.numTools = 3
    item.federationMetadata = {}

    plan = _make_sync_plan(
        federation_id,
        FederationProviderType.AWS_AGENTCORE,
        mcp_updates=[(existing, item, "arn:mcp:1")],
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service.acl_service.grant_permission = AsyncMock()

    await federation_sync_service._apply_sync_plan(plan, user_id="user-kent")

    federation_sync_service.acl_service.grant_permission.assert_awaited_once_with(
        principal_type="user",
        principal_id="user-kent",
        resource_type=ResourceType.MCPSERVER,
        resource_id=existing_id,
        perm_bits=RoleBits.OWNER,
    )


@pytest.mark.asyncio
async def test_apply_sync_plan_skips_acl_grant_when_no_user_id(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """No ACL grant is made when the sync has no user context (e.g. scheduled system sync)."""
    federation_id = PydanticObjectId()

    server = MagicMock()
    server.id = PydanticObjectId()
    server.federationMetadata = {}
    server.insert = AsyncMock()

    plan = _make_sync_plan(
        federation_id,
        FederationProviderType.AWS_AGENTCORE,
        mcp_creates=[(server, "arn:mcp:1")],
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service.acl_service.grant_permission = AsyncMock()

    await federation_sync_service._apply_sync_plan(plan, user_id=None)

    federation_sync_service.acl_service.grant_permission.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_sync_plan_continues_after_acl_grant_failure(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """ACL grant failures are logged but do not abort the sync."""
    federation_id = PydanticObjectId()

    server = MagicMock()
    server.id = PydanticObjectId()
    server.federationMetadata = {}
    server.insert = AsyncMock()

    plan = _make_sync_plan(
        federation_id,
        FederationProviderType.AWS_AGENTCORE,
        mcp_creates=[(server, "arn:mcp:1")],
    )

    monkeypatch.setattr("registry.services.federation_sync_service.get_current_session", lambda: None)
    federation_sync_service.acl_service.grant_permission = AsyncMock(side_effect=Exception("ACL store down"))

    # Should not raise
    result = await federation_sync_service._apply_sync_plan(plan, user_id="user-yulin")

    assert result.summary.createdMcpServers == 0  # summary unchanged (tracked by _build_sync_plan)
    server.insert.assert_awaited_once()
