from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.api.v1.a2a.agent_routes import create_agent, delete_agent, get_agent_stats, list_agents, update_agent
from registry.schemas.a2a_agent_api_schemas import (
    AgentCreateRequest,
    AgentUpdateRequest,
    convert_to_detail,
    convert_to_list_item,
)
from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import RoleBits


class _RecordingTxnCtx:
    def __init__(self):
        self.exit_exc_type: object = "unset"

    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_exc_type = exc_type
        return False  # do not suppress -> exception propagates out of @use_transaction


class _FakeTxnSession:
    def __init__(self, ctx: _RecordingTxnCtx):
        self._ctx = ctx

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def start_transaction(self):
        return self._ctx


class _FakeTxnClient:
    def __init__(self, session: _FakeTxnSession):
        self._session = session

    def start_session(self):
        return self._session


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
        config=SimpleNamespace(
            title="Test Agent",
            description="Agent description",
            url="https://agent.example.com",
            type="jsonrpc",
            enabled=True,
        ),
        tags=["test"],
        author=PydanticObjectId(),
        createdAt=now,
        updatedAt=now,
        wellKnown=SimpleNamespace(
            enabled=False,
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
        page=1,
        per_page=20,
        acl_service=acl_service,
        a2a_agent_service=a2a_agent_service,
    )

    acl_service.get_accessible_resource_ids.assert_awaited_once()
    a2a_agent_service.list_agents.assert_awaited_once()
    # status filtering is gone; the route always calls the service with enabled_only=False
    assert a2a_agent_service.list_agents.await_args.kwargs["enabled_only"] is False
    assert result.pagination.total == 1
    assert result.agents[0].name == "Test Agent"
    # Assert config field is returned
    assert result.agents[0].config is not None
    assert result.agents[0].config.title == "Test Agent"
    assert result.agents[0].config.type == "jsonrpc"


@pytest.mark.asyncio
async def test_get_agent_stats_uses_injected_service(sample_user_context):
    a2a_agent_service = MagicMock()
    a2a_agent_service.get_stats = AsyncMock(
        return_value={
            "total_agents": 3,
            "enabled_agents": 2,
            "disabled_agents": 1,
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
        title="Test Agent",
        description="Agent description",
        url="https://agent.example.com",
        type="jsonrpc",
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
    # Assert config field is returned with correct values
    assert result.config is not None
    assert result.config.title == "Test Agent"
    assert result.config.description == "Agent description"
    assert result.config.type == "jsonrpc"


@pytest.mark.asyncio
async def test_create_agent_invalid_root_path_returns_400(sample_user_context):
    from fastapi import HTTPException

    a2a_agent_service = MagicMock()
    a2a_agent_service.create_agent = AsyncMock(
        side_effect=ValueError("A2A agent path must contain at least one letter or number and cannot be '/'")
    )

    acl_service = MagicMock()
    acl_service.grant_permission = AsyncMock()

    request = AgentCreateRequest(
        path="/",
        title="Root Agent",
        description="Agent description",
        url="https://agent.example.com",
        type="jsonrpc",
    )

    with patch("registry_pkgs.database.decorators.MongoDB.get_client") as mock_get_client:
        mock_session = AsyncMock()
        mock_client = MagicMock()
        mock_client.start_session.return_value.__aenter__.return_value = mock_session
        mock_session.start_transaction.return_value.__aenter__.return_value = None
        mock_get_client.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await create_agent(
                data=request,
                user_context=sample_user_context,
                acl_service=acl_service,
                a2a_agent_service=a2a_agent_service,
            )

    assert exc_info.value.status_code == 400
    assert "invalid_request" in str(exc_info.value.detail)
    acl_service.grant_permission.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_duplicate_path_returns_409(sample_user_context):
    from fastapi import HTTPException

    agent_id = str(PydanticObjectId())
    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock(return_value=15)

    a2a_agent_service = MagicMock()
    a2a_agent_service.update_agent = AsyncMock(
        side_effect=ValueError("An agent with path 'team-a-crm-agent' already exists. Please choose a different path.")
    )

    request = AgentUpdateRequest(path="/Team A/CRM Agent")

    with patch("registry_pkgs.database.decorators.MongoDB.get_client") as mock_get_client:
        mock_session = AsyncMock()
        mock_client = MagicMock()
        mock_client.start_session.return_value.__aenter__.return_value = mock_session
        mock_session.start_transaction.return_value.__aenter__.return_value = None
        mock_get_client.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await update_agent(
                agent_id=agent_id,
                data=request,
                user_context=sample_user_context,
                acl_service=acl_service,
                a2a_agent_service=a2a_agent_service,
            )

    assert exc_info.value.status_code == 409
    assert "duplicate_entry" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_update_agent_invalid_root_path_returns_400(sample_user_context):
    from fastapi import HTTPException

    agent_id = str(PydanticObjectId())
    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock(return_value=15)

    a2a_agent_service = MagicMock()
    a2a_agent_service.update_agent = AsyncMock(
        side_effect=ValueError("A2A agent path must contain at least one letter or number and cannot be '/'")
    )

    request = AgentUpdateRequest(path="/")

    with patch("registry_pkgs.database.decorators.MongoDB.get_client") as mock_get_client:
        mock_session = AsyncMock()
        mock_client = MagicMock()
        mock_client.start_session.return_value.__aenter__.return_value = mock_session
        mock_session.start_transaction.return_value.__aenter__.return_value = None
        mock_get_client.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await update_agent(
                agent_id=agent_id,
                data=request,
                user_context=sample_user_context,
                acl_service=acl_service,
                a2a_agent_service=a2a_agent_service,
            )

    assert exc_info.value.status_code == 400
    assert "invalid_request" in str(exc_info.value.detail)


# ==================== refresh_agent_capabilities endpoint tests ====================


@pytest.mark.asyncio
async def test_refresh_agent_capabilities_success(sample_user_context):
    """Test successful agent capabilities refresh."""

    from registry.api.v1.a2a.agent_routes import refresh_agent_capabilities
    from registry.schemas.acl_schema import ResourcePermissions

    agent_id = str(PydanticObjectId())
    mock_agent = _build_agent(PydanticObjectId(agent_id))

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(
        return_value=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)
    )

    mock_a2a_agent_service = MagicMock()
    mock_a2a_agent_service.refresh_agent_capabilities = AsyncMock(return_value=mock_agent)

    result = await refresh_agent_capabilities(
        agent_id=agent_id,
        user_context=sample_user_context,
        acl_service=mock_acl_service,
        a2a_agent_service=mock_a2a_agent_service,
    )

    # Verify ACL check used EDIT permission
    mock_acl_service.check_user_permission.assert_awaited_once()
    call_args = mock_acl_service.check_user_permission.call_args
    assert call_args.kwargs["required_permission"] == "EDIT"

    # Verify service was called
    mock_a2a_agent_service.refresh_agent_capabilities.assert_awaited_once_with(agent_id=agent_id)

    # Verify response
    assert result.name == "Test Agent"


@pytest.mark.asyncio
async def test_refresh_agent_capabilities_not_found():
    """Test 404 response when agent is not found."""
    from fastapi import HTTPException

    from registry.api.v1.a2a.agent_routes import refresh_agent_capabilities

    agent_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())

    mock_a2a_agent_service = MagicMock()
    mock_a2a_agent_service.refresh_agent_capabilities = AsyncMock(side_effect=ValueError("Agent not found"))

    with pytest.raises(HTTPException) as exc_info:
        await refresh_agent_capabilities(
            agent_id=agent_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            a2a_agent_service=mock_a2a_agent_service,
        )

    # Verify 404 error
    assert exc_info.value.status_code == 404
    assert "resource_not_found" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_refresh_agent_capabilities_transport_error():
    """Test 503 response on network/transport error."""
    from fastapi import HTTPException

    from registry.api.v1.a2a.agent_routes import refresh_agent_capabilities
    from registry.core.exceptions import A2AAgentCardTransportException

    agent_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())

    mock_a2a_agent_service = MagicMock()
    mock_a2a_agent_service.refresh_agent_capabilities = AsyncMock(
        side_effect=A2AAgentCardTransportException("Connection timeout")
    )

    with pytest.raises(HTTPException) as exc_info:
        await refresh_agent_capabilities(
            agent_id=agent_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            a2a_agent_service=mock_a2a_agent_service,
        )

    # Verify 503 error
    assert exc_info.value.status_code == 503
    assert "service_unavailable" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_refresh_agent_capabilities_upstream_error():
    """Test 502 response on upstream service error."""
    from fastapi import HTTPException

    from registry.api.v1.a2a.agent_routes import refresh_agent_capabilities
    from registry.core.exceptions import A2AAgentCardUpstreamException

    agent_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())

    mock_a2a_agent_service = MagicMock()
    mock_a2a_agent_service.refresh_agent_capabilities = AsyncMock(
        side_effect=A2AAgentCardUpstreamException("Upstream returned 500")
    )

    with pytest.raises(HTTPException) as exc_info:
        await refresh_agent_capabilities(
            agent_id=agent_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            a2a_agent_service=mock_a2a_agent_service,
        )

    # Verify 502 error
    assert exc_info.value.status_code == 502
    assert "external_service_error" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_refresh_agent_capabilities_invalid_request():
    """Test 400 response on invalid request (agent well-known not enabled)."""
    from fastapi import HTTPException

    from registry.api.v1.a2a.agent_routes import refresh_agent_capabilities

    agent_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())

    mock_a2a_agent_service = MagicMock()
    mock_a2a_agent_service.refresh_agent_capabilities = AsyncMock(
        side_effect=ValueError("Well-known sync is not enabled for this agent")
    )

    with pytest.raises(HTTPException) as exc_info:
        await refresh_agent_capabilities(
            agent_id=agent_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            a2a_agent_service=mock_a2a_agent_service,
        )

    # Verify 400 error with correct error code
    assert exc_info.value.status_code == 400
    assert "invalid_request" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_refresh_agent_capabilities_permission_denied():
    """Test that the refresh endpoint requires EDIT permission."""
    from fastapi import HTTPException

    from registry.api.v1.a2a.agent_routes import refresh_agent_capabilities
    from registry.schemas.errors import ErrorCode, create_error_detail

    agent_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    # Simulate permission denied using standard error format
    mock_acl_service.check_user_permission = AsyncMock(
        side_effect=HTTPException(
            status_code=403, detail=create_error_detail(ErrorCode.INSUFFICIENT_PERMISSIONS, "Forbidden")
        )
    )

    mock_a2a_agent_service = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await refresh_agent_capabilities(
            agent_id=agent_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            a2a_agent_service=mock_a2a_agent_service,
        )

    # Verify 403 is propagated (HTTPException is re-raised as-is by the route handler)
    assert exc_info.value.status_code == 403

    # Verify service was never called (permission check failed first)
    mock_a2a_agent_service.refresh_agent_capabilities.assert_not_called()


@pytest.mark.asyncio
async def test_create_agent_rolls_back_when_grant_permission_fails(sample_user_context):
    """AC4: if grant_permission fails after create_agent succeeds, the failure
    propagates out of the @use_transaction boundary (rollback path), so the
    agent insert is rolled back rather than left orphaned."""
    agent = _build_agent()
    a2a_agent_service = MagicMock()
    a2a_agent_service.create_agent = AsyncMock(return_value=agent)

    acl_service = MagicMock()
    acl_service.grant_permission = AsyncMock(side_effect=RuntimeError("ACL write failed"))

    request = AgentCreateRequest(
        path="/test-agent",
        title="Test Agent",
        description="Agent description",
        url="https://agent.example.com",
        type="jsonrpc",
    )

    ctx = _RecordingTxnCtx()
    fake_client = _FakeTxnClient(_FakeTxnSession(ctx))

    with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc_info:
            await create_agent(
                data=request,
                user_context=sample_user_context,
                acl_service=acl_service,
                a2a_agent_service=a2a_agent_service,
            )

    assert exc_info.value.status_code == 500
    a2a_agent_service.create_agent.assert_awaited_once()
    acl_service.grant_permission.assert_awaited_once()
    # Transaction context manager exited via an exception -> rollback path taken.
    assert ctx.exit_exc_type is HTTPException


@pytest.mark.asyncio
async def test_delete_agent_rolls_back_when_acl_cleanup_fails(sample_user_context):
    agent_id = str(PydanticObjectId())

    a2a_agent_service = MagicMock()
    a2a_agent_service.delete_agent = AsyncMock(return_value=True)

    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    acl_service.delete_acl_entries_for_resource = AsyncMock(side_effect=RuntimeError("ACL delete failed"))

    ctx = _RecordingTxnCtx()
    fake_client = _FakeTxnClient(_FakeTxnSession(ctx))

    with patch("registry_pkgs.database.decorators.MongoDB.get_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc_info:
            await delete_agent(
                agent_id=agent_id,
                user_context=sample_user_context,
                acl_service=acl_service,
                a2a_agent_service=a2a_agent_service,
            )

    assert exc_info.value.status_code == 500
    a2a_agent_service.delete_agent.assert_awaited_once()
    acl_service.delete_acl_entries_for_resource.assert_awaited_once()
    # Transaction context manager exited via an exception -> rollback path taken.
    assert ctx.exit_exc_type is HTTPException


def test_convert_to_list_item_uses_config_enabled_without_status():
    agent = _build_agent()
    agent.config.enabled = False
    item = convert_to_list_item(agent, 15)
    assert not hasattr(item, "status")
    assert item.enabled is False


def test_convert_to_detail_uses_config_enabled_without_status():
    agent = _build_agent()
    agent.config.enabled = True
    detail = convert_to_detail(agent, 15)
    assert not hasattr(detail, "status")
    assert detail.enabled is True
