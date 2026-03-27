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
    job = SimpleNamespace(id=PydanticObjectId())

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
    federation_sync_service.federation_crud_service.mark_sync_pending.assert_awaited_once_with(updated)
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
async def test_sync_vector_index_after_commit_logs_and_continues_on_vector_failure(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    server = SimpleNamespace(serverName="server-demo")
    agent = SimpleNamespace(card=SimpleNamespace(name="agent-demo"))
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(),
        created_mcp=[server],
        deleted_a2a=[agent],
    )

    federation_sync_service.mcp_server_repo.sync_server_to_vector_db = AsyncMock(
        side_effect=RuntimeError("vector down")
    )
    federation_sync_service.a2a_agent_repo.sync_agent_to_vector_db = AsyncMock(
        return_value={"indexed": 0, "failed": 0, "deleted": 1}
    )

    await federation_sync_service._sync_vector_index_after_commit(
        federation=federation,
        job=job,
        mutation_result=mutation_result,
    )

    federation_sync_service.mcp_server_repo.sync_server_to_vector_db.assert_awaited_once_with(server, is_delete=False)
    federation_sync_service.a2a_agent_repo.sync_agent_to_vector_db.assert_awaited_once_with(agent, is_delete=True)
