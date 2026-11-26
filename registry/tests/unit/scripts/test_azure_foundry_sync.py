import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from scripts import azure_foundry_sync as script


async def test_cancelled_federation_cleanup_still_closes_infrastructure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2E_KEEP_FEDERATION", raising=False)
    federation = Mock(id="test-federation", providerConfig={})
    container = Mock(shutdown=AsyncMock())
    container.federation_crud_service.create_federation = AsyncMock(return_value=federation)
    container.federation_sync_service.start_manual_sync = AsyncMock()
    agents = Mock(to_list=AsyncMock(return_value=[]), delete=AsyncMock(side_effect=asyncio.CancelledError))

    with (
        patch.object(script, "load_dotenv"),
        patch.object(script, "_require_env", return_value="test"),
        patch.object(script, "init_mongodb"),
        patch.object(script, "close_mongodb") as close_mongo,
        patch.object(script, "create_async_redis_client") as create_async_redis,
        patch.object(script, "close_async_redis_client") as close_async_redis,
        patch.object(script, "create_redis_client") as create_sync_redis,
        patch.object(script, "close_redis_client") as close_sync_redis,
        patch.object(script, "resolve_vector_backend_config"),
        patch.object(script, "create_database_client") as create_vector,
        patch.object(script, "RegistryContainer", return_value=container),
        patch.object(script.Federation, "find", return_value=Mock(to_list=AsyncMock(return_value=[]))),
        patch.object(script.Federation, "get", new=AsyncMock(return_value=federation)),
        patch.object(script.A2AAgent, "find", return_value=agents),
        pytest.raises(asyncio.CancelledError),
    ):
        await script.main()

    agents.delete.assert_awaited_once_with()
    container.shutdown.assert_awaited_once_with()
    create_vector.return_value.close.assert_called_once_with()
    close_sync_redis.assert_called_once_with(create_sync_redis.return_value)
    close_async_redis.assert_awaited_once_with(create_async_redis.return_value)
    close_mongo.assert_awaited_once_with()
