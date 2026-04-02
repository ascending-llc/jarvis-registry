from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from registry.api.v1.federation.agentcore_routes import (
    AgentCoreRuntimeSyncRequest,
    sync_agentcore_runtime,
)

TEST_USER_ID = "000000000000000000000001"


@pytest.fixture
def sample_user_context():
    return {
        "user_id": TEST_USER_ID,
        "username": "testuser",
    }


@pytest.mark.asyncio
async def test_sync_agentcore_runtime_uses_injected_service(sample_user_context):
    agentcore_import_service = MagicMock()
    agentcore_import_service.import_from_runtime = AsyncMock(
        return_value={
            "runtime_filter_count": 1,
            "discovered": {"mcp_servers": 1, "a2a_agents": 0, "skipped_runtimes": 0},
            "created": {"mcp_servers": 1, "a2a_agents": 0},
            "updated": {"mcp_servers": 0, "a2a_agents": 0},
            "deleted": {"mcp_servers": 0, "a2a_agents": 0},
            "skipped": {"mcp_servers": 0, "a2a_agents": 0},
            "errors": [],
            "mcp_servers": [],
            "a2a_agents": [],
            "skipped_runtimes": [],
            "duration_seconds": 0.5,
        }
    )

    result = await sync_agentcore_runtime(
        data=AgentCoreRuntimeSyncRequest(
            dryRun=True,
            awsRegion="us-west-2",
            runtimeArn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        ),
        user_context=sample_user_context,
        agentcore_import_service=agentcore_import_service,
    )

    agentcore_import_service.import_from_runtime.assert_awaited_once_with(
        dry_run=True,
        user_id=sample_user_context["user_id"],
        aws_region="us-west-2",
        runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
    )
    assert result.runtime_filter_count == 1
    assert result.created.mcp_servers == 1


@pytest.mark.asyncio
async def test_sync_agentcore_runtime_maps_value_error(sample_user_context):
    agentcore_import_service = MagicMock()
    agentcore_import_service.import_from_runtime = AsyncMock(side_effect=ValueError("bad request"))

    with pytest.raises(HTTPException) as exc_info:
        await sync_agentcore_runtime(
            data=AgentCoreRuntimeSyncRequest(dryRun=False),
            user_context=sample_user_context,
            agentcore_import_service=agentcore_import_service,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_request"
