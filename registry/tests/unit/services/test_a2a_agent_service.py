from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentUpdateRequest
from registry.services.a2a_agent_service import A2AAgentService

_SENTINEL_SESSION = object()


def _service() -> A2AAgentService:
    # No repo -> the _schedule_* helpers return early (no asyncio.create_task).
    return A2AAgentService(a2a_agent_repo=None)


def _patch_session():
    return patch(
        "registry.services.a2a_agent_service.get_current_session",
        return_value=_SENTINEL_SESSION,
    )


@pytest.mark.asyncio
async def test_create_agent_passes_session_to_insert():
    service = _service()
    request = AgentCreateRequest(
        path="/test-agent",
        title="Test Agent",
        description="desc",
        url="https://agent.example.com",
        type="jsonrpc",
    )
    mock_card = SimpleNamespace(version="1.0.0", name="Test Agent", description="desc")

    with (
        _patch_session(),
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_fetch_agent_card_from_url", AsyncMock(return_value=mock_card)),
    ):
        MockAgent.find_one = AsyncMock(return_value=None)  # no existing path
        agent_instance = MockAgent.return_value
        agent_instance.insert = AsyncMock()
        agent_instance.id = PydanticObjectId()
        agent_instance.config = SimpleNamespace(title="Test Agent")
        agent_instance.path = "/test-agent"

        await service.create_agent(data=request, user_id=str(PydanticObjectId()))

    agent_instance.insert.assert_awaited_once()
    assert agent_instance.insert.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_update_agent_passes_session_to_save():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.config = SimpleNamespace(title="old", url="https://agent.example.com")
    fake_agent.vectorContentHash = "hash"

    data = AgentUpdateRequest(title="New Title")  # title-only -> no card fetch

    with (
        _patch_session(),
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.update_agent(agent_id=str(PydanticObjectId()), data=data)

    fake_agent.save.assert_awaited_once()
    assert fake_agent.save.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_delete_agent_passes_session_to_delete():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.delete = AsyncMock()
    fake_agent.card = SimpleNamespace(name="Test Agent")

    with (
        _patch_session(),
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        result = await service.delete_agent(agent_id=str(PydanticObjectId()))

    assert result is True
    fake_agent.delete.assert_awaited_once()
    assert fake_agent.delete.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_toggle_agent_status_passes_session_to_save():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.card = SimpleNamespace(name="Test Agent")
    fake_agent.vectorContentHash = "hash"

    with (
        _patch_session(),
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.toggle_agent_status(agent_id=str(PydanticObjectId()), enabled=True)

    fake_agent.save.assert_awaited_once()
    assert fake_agent.save.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_sync_wellknown_passes_session_to_save():
    service = _service()
    old_card = SimpleNamespace(version="1.0.0", description="old", skills=[], capabilities={}, name="Test Agent")
    updated_card = SimpleNamespace(version="2.0.0", description="new", skills=[], capabilities={}, name="Test Agent")

    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.card = old_card
    fake_agent.config = SimpleNamespace(url="https://agent.example.com")
    fake_agent.wellKnown = SimpleNamespace(
        enabled=True,
        lastSyncAt=datetime.now(UTC),
        lastSyncStatus="success",
        lastSyncVersion="1.0.0",
        syncError=None,
    )

    with (
        _patch_session(),
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_resolve_agent_card_with_fallback", AsyncMock(return_value=updated_card)),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.sync_wellknown(agent_id=str(PydanticObjectId()))

    fake_agent.save.assert_awaited_once()
    assert fake_agent.save.await_args.kwargs["session"] is _SENTINEL_SESSION
