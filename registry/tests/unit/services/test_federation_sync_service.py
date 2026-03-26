from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation.sync_handlers import AwsAgentCoreSyncHandler, AzureAiFoundrySyncHandler
from registry.services.federation_sync_service import FederationSyncService
from registry_pkgs.models.enums import FederationProviderType, FederationStatus, FederationSyncStatus


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
    handler = AwsAgentCoreSyncHandler()
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole",
            "resourceTagsFilter": {"env": "production", "team": "platform"},
        },
    )
    fake_client = MagicMock()
    fake_client.discover_runtime_entities = AsyncMock(
        return_value={"mcp_servers": [], "a2a_agents": [], "skipped_runtimes": []}
    )
    handler.build_client = MagicMock(return_value=fake_client)

    result = await handler.discover_entities(federation)

    fake_client.discover_runtime_entities.assert_awaited_once_with(
        author_id=None,
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
