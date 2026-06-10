from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentUpdateRequest
from registry.services.a2a_agent_service import A2AAgentService
from registry_pkgs.models.a2a_agent import A2AAgent


@pytest.fixture
def agent_document() -> SimpleNamespace:
    agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="existing-agent",
        config=SimpleNamespace(title="Existing Agent", url="https://agent.example.com", type="jsonrpc", enabled=False),
        card=SimpleNamespace(url="https://agent.example.com", name="Existing Agent", description="desc"),
        wellKnown=None,
        updatedAt=None,
        vectorContentHash="old-hash",
    )
    agent.save = AsyncMock(return_value=None)
    return agent


@pytest.mark.asyncio
async def test_get_agent_by_path_queries_exact_slug(monkeypatch):
    service = A2AAgentService()
    find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(A2AAgent, "find_one", find_one)

    result = await service.get_agent_by_path("team-a-crm-agent")

    assert result is None
    find_one.assert_awaited_once_with({"path": "team-a-crm-agent"})


@pytest.mark.asyncio
async def test_get_agent_by_path_does_not_normalize_invalid_slug(monkeypatch):
    service = A2AAgentService()
    find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(A2AAgent, "find_one", find_one)

    result = await service.get_agent_by_path("/Team A/CRM Agent")

    assert result is None
    find_one.assert_awaited_once_with({"path": "/Team A/CRM Agent"})


@pytest.mark.asyncio
async def test_create_agent_duplicate_check_uses_normalized_path(monkeypatch):
    service = A2AAgentService()
    existing_agent = SimpleNamespace(id=PydanticObjectId())
    find_one = AsyncMock(return_value=existing_agent)
    fetch_card = AsyncMock()

    monkeypatch.setattr(A2AAgent, "find_one", find_one)
    monkeypatch.setattr(service, "_fetch_agent_card_from_url", fetch_card)

    request = AgentCreateRequest(
        path="/AgentCore/A2A/My Agent!",
        title="My Agent",
        description="desc",
        url="https://agent.example.com",
        type="jsonrpc",
    )

    with pytest.raises(ValueError, match="agentcore-a2a-my-agent"):
        await service.create_agent(request, str(PydanticObjectId()))

    find_one.assert_awaited_once_with({"path": "agentcore-a2a-my-agent"})
    fetch_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_rejects_root_path_before_database_lookup(monkeypatch):
    service = A2AAgentService()
    find_one = AsyncMock()
    fetch_card = AsyncMock()

    monkeypatch.setattr(A2AAgent, "find_one", find_one)
    monkeypatch.setattr(service, "_fetch_agent_card_from_url", fetch_card)

    request = AgentCreateRequest(
        path="/",
        title="Root Agent",
        description="desc",
        url="https://agent.example.com",
        type="jsonrpc",
    )

    with pytest.raises(ValueError, match="cannot be '/'"):
        await service.create_agent(request, str(PydanticObjectId()))

    find_one.assert_not_awaited()
    fetch_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_normalizes_path_before_saving(monkeypatch, agent_document):
    service = A2AAgentService()
    get_agent = AsyncMock(return_value=agent_document)
    find_one = AsyncMock(return_value=None)

    monkeypatch.setattr(A2AAgent, "get", get_agent)
    monkeypatch.setattr(A2AAgent, "find_one", find_one)

    request = AgentUpdateRequest(path="/Team A/CRM Agent v2")
    result = await service.update_agent(str(agent_document.id), request)

    assert result.path == "team-a-crm-agent-v2"
    find_one.assert_awaited_once_with({"path": "team-a-crm-agent-v2", "_id": {"$ne": agent_document.id}})
    agent_document.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_agent_rejects_root_path_before_conflict_check(monkeypatch, agent_document):
    service = A2AAgentService()
    get_agent = AsyncMock(return_value=agent_document)
    find_one = AsyncMock()

    monkeypatch.setattr(A2AAgent, "get", get_agent)
    monkeypatch.setattr(A2AAgent, "find_one", find_one)

    request = AgentUpdateRequest(path="/")

    with pytest.raises(ValueError, match="cannot be '/'"):
        await service.update_agent(str(agent_document.id), request)

    find_one.assert_not_awaited()
    agent_document.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_duplicate_check_uses_normalized_path(monkeypatch, agent_document):
    service = A2AAgentService()
    conflicting_agent = SimpleNamespace(id=PydanticObjectId())

    monkeypatch.setattr(A2AAgent, "get", AsyncMock(return_value=agent_document))
    monkeypatch.setattr(A2AAgent, "find_one", AsyncMock(return_value=conflicting_agent))

    request = AgentUpdateRequest(path="/Team A/CRM Agent v2")

    with pytest.raises(ValueError, match="team-a-crm-agent-v2"):
        await service.update_agent(str(agent_document.id), request)

    agent_document.save.assert_not_awaited()
