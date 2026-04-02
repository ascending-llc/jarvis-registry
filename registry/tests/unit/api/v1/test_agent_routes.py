from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.api.v1.a2a.agent_routes import create_agent, get_agent_stats, list_agents
from registry.schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentSkillInput
from registry_pkgs.models._generated import PrincipalType, ResourceType
from registry_pkgs.models.enums import RoleBits


def _build_agent(agent_id: PydanticObjectId | None = None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=agent_id or PydanticObjectId(),
        path="/test-agent",
        card=SimpleNamespace(
            name="Test Agent",
            description="Agent description",
            url="https://agent.example.com",
            version="1.0.0",
            protocol_version="1.0",
            skills=[SimpleNamespace(id="skill-1", name="Skill 1", description="desc", tags=[])],
            preferred_transport="HTTP+JSON",
            capabilities={},
            security_schemes={},
            default_input_modes=["text/plain"],
            default_output_modes=["application/json"],
            provider=None,
        ),
        tags=["test"],
        isEnabled=True,
        status="active",
        author=PydanticObjectId(),
        createdAt=now,
        updatedAt=now,
        wellKnown=SimpleNamespace(
            enabled=False,
            url=None,
            lastSyncAt=None,
            lastSyncStatus=None,
            lastSyncVersion=None,
        ),
    )


@pytest.fixture
def sample_user_context():
    return {
        "user_id": str(PydanticObjectId()),
        "username": "testuser",
        "scopes": ["mcp-registry-admin"],
    }


@pytest.mark.asyncio
async def test_list_agents_uses_injected_services(sample_user_context):
    agent = _build_agent()
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[str(agent.id)])
    acl_service.get_user_permissions_for_resource = AsyncMock(return_value=15)

    a2a_agent_service = MagicMock()
    a2a_agent_service.list_agents = AsyncMock(return_value=([agent], 1))

    result = await list_agents(
        user_context=sample_user_context,
        query="test",
        status="active",
        page=1,
        per_page=20,
        acl_service=acl_service,
        a2a_agent_service=a2a_agent_service,
    )

    acl_service.get_accessible_resource_ids.assert_awaited_once()
    a2a_agent_service.list_agents.assert_awaited_once()
    assert result.pagination.total == 1
    assert result.agents[0].name == "Test Agent"


@pytest.mark.asyncio
async def test_get_agent_stats_uses_injected_service(sample_user_context):
    a2a_agent_service = MagicMock()
    a2a_agent_service.get_stats = AsyncMock(
        return_value={
            "total_agents": 3,
            "enabled_agents": 2,
            "disabled_agents": 1,
            "by_status": {"active": 2, "inactive": 1},
            "by_transport": {"HTTP+JSON": 3},
            "total_skills": 5,
            "average_skills_per_agent": 1.7,
        }
    )

    result = await get_agent_stats(
        user_context=sample_user_context,
        a2a_agent_service=a2a_agent_service,
    )

    a2a_agent_service.get_stats.assert_awaited_once()
    assert result.totalAgents == 3
    assert result.totalSkills == 5


@pytest.mark.asyncio
async def test_create_agent_uses_injected_services(sample_user_context):
    agent = _build_agent()
    a2a_agent_service = MagicMock()
    a2a_agent_service.create_agent = AsyncMock(return_value=agent)

    acl_service = MagicMock()
    acl_service.grant_permission = AsyncMock(return_value=MagicMock())

    request = AgentCreateRequest(
        path="/test-agent",
        name="Test Agent",
        description="Agent description",
        url="https://agent.example.com",
        version="1.0.0",
        skills=[AgentSkillInput(id="skill-1", name="Skill 1", description="desc")],
        tags=["test"],
        enabled=True,
    )

    with patch("registry_pkgs.database.decorators.MongoDB.get_client") as mock_get_client:
        mock_session = AsyncMock()
        mock_client = MagicMock()
        mock_client.start_session.return_value.__aenter__.return_value = mock_session
        mock_session.start_transaction.return_value.__aenter__.return_value = None
        mock_get_client.return_value = mock_client

        result = await create_agent(
            data=request,
            user_context=sample_user_context,
            acl_service=acl_service,
            a2a_agent_service=a2a_agent_service,
        )

    a2a_agent_service.create_agent.assert_awaited_once_with(data=request, user_id=sample_user_context["user_id"])
    acl_service.grant_permission.assert_awaited_once()
    call_args = acl_service.grant_permission.call_args
    assert call_args.kwargs["principal_type"] == PrincipalType.USER
    assert call_args.kwargs["resource_type"] == ResourceType.REMOTE_AGENT
    assert call_args.kwargs["perm_bits"] == RoleBits.OWNER
    assert result.name == "Test Agent"
