from unittest.mock import AsyncMock, Mock

import pytest

from registry.services.oauth.mcp_service import MCPService


@pytest.mark.asyncio
async def test_initialize_checks_flow_state_storage_once():
    oauth_service = Mock()
    oauth_service.flow_manager.initialize = AsyncMock()

    await MCPService(oauth_service).initialize()

    oauth_service.flow_manager.initialize.assert_awaited_once()
