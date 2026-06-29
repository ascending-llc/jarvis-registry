from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.api.v1.server.server_routes import create_server
from registry.schemas.server_api_schemas import ServerCreateRequest, convert_to_detail, convert_to_list_item
from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import RoleBits


@pytest.fixture
def sample_user_context():
    return {
        "user_id": PydanticObjectId(),
        "username": "testuser",
        "acl_permission_map": {},
    }


@pytest.fixture
def sample_server_request():
    return ServerCreateRequest(
        title="Test Server",
        path="/testserver",
        tags=["test"],
        url="http://localhost:8000",
        description="Test server description",
        supported_transports=["streamable-http"],
        timeout=None,
        init_timeout=None,
        server_instructions=None,
        oauth=None,
        apiKey=None,
        custom_user_vars=None,
        tool_list=[],
        requires_oauth=False,
    )


@pytest.fixture
def mock_created_server():
    mock_server = MagicMock()
    mock_server.id = PydanticObjectId()
    mock_server.serverName = "test-server"
    mock_server.config = {"title": "Test Server"}
    return mock_server


@pytest.mark.asyncio
async def test_create_server_route_creates_acl_entry(
    sample_server_request,
    sample_user_context,
    mock_created_server,
):
    # Mock the transaction session
    mock_session = AsyncMock()
    mock_server_service = MagicMock()
    mock_server_service.create_server = AsyncMock(return_value=mock_created_server)
    mock_acl_service = MagicMock()
    mock_acl_service.grant_permission = AsyncMock(return_value=MagicMock())

    with (
        patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client,
        patch(
            "registry.api.v1.server.server_routes.convert_to_detail",
            return_value={"id": str(mock_created_server.id)},
        ),
    ):
        # Mock the MongoDB client and session for the explicit transaction block
        mock_client = MagicMock()
        mock_client.start_session.return_value.__aenter__.return_value = mock_session
        mock_session.start_transaction.return_value.__aenter__.return_value = None
        mock_get_client.return_value = mock_client

        await create_server(
            data=sample_server_request,
            user_context=sample_user_context,
            server_service=mock_server_service,
            acl_service=mock_acl_service,
        )

        # Verify server creation was called correctly
        mock_server_service.create_server.assert_awaited_once_with(
            data=sample_server_request,
            user_id=sample_user_context["user_id"],
            session=mock_session,
        )

        # Verify ACL permission was granted
        mock_acl_service.grant_permission.assert_awaited_once()

        # Verify ACL call has correct parameters
        call_args = mock_acl_service.grant_permission.call_args
        assert call_args.kwargs["principal_type"] == PrincipalType.USER
        assert call_args.kwargs["principal_id"] == PydanticObjectId(sample_user_context["user_id"])
        assert call_args.kwargs["resource_type"] == ResourceType.MCPSERVER
        assert call_args.kwargs["resource_id"] == mock_created_server.id
        assert call_args.kwargs["perm_bits"] == RoleBits.OWNER
        assert call_args.kwargs["session"] is mock_session


# ==================== refresh_server_capabilities endpoint tests ====================


@pytest.mark.asyncio
async def test_refresh_server_capabilities_success(sample_user_context):
    """Test successful server capabilities refresh"""

    from registry.api.v1.server.server_routes import refresh_server_capabilities
    from registry.schemas.acl_schema import ResourcePermissions

    server_id = str(PydanticObjectId())
    mock_server = MagicMock()
    mock_server.id = PydanticObjectId(server_id)
    mock_server.serverName = "test-server"

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(
        return_value=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)
    )

    mock_server_service = MagicMock()
    mock_server_service.refresh_server_capabilities = AsyncMock(
        return_value={
            "server": mock_server,
            "status": "success",
            "status_message": "Capabilities refreshed successfully",
            "last_checked": "2026-01-01T00:00:00Z",
            "response_time_ms": None,
        }
    )

    with patch(
        "registry.api.v1.server.server_routes.convert_to_detail",
        return_value={"id": server_id, "serverName": "test-server"},
    ):
        result = await refresh_server_capabilities(
            server_id=server_id,
            user_context=sample_user_context,
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

        # Verify ACL check was called with EDIT permission
        mock_acl_service.check_user_permission.assert_awaited_once()
        call_args = mock_acl_service.check_user_permission.call_args
        assert call_args.kwargs["required_permission"] == "EDIT"

        # Verify service was called
        mock_server_service.refresh_server_capabilities.assert_awaited_once_with(
            server_id=server_id,
            user_id=sample_user_context["user_id"],
        )

        # Verify response
        assert result["id"] == server_id


@pytest.mark.asyncio
async def test_refresh_server_capabilities_failed():
    """Test failed server capabilities refresh returns HTTP 400"""
    from fastapi import HTTPException

    from registry.api.v1.server.server_routes import refresh_server_capabilities

    server_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())

    mock_server_service = MagicMock()
    mock_server_service.refresh_server_capabilities = AsyncMock(
        return_value={
            "status": "failed",
            "status_message": "Failed to connect to server",
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await refresh_server_capabilities(
            server_id=server_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

    # Verify HTTP 400 error
    assert exc_info.value.status_code == 400
    assert "capabilities_refresh_failed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_refresh_server_capabilities_permission_denied():
    """Test refresh endpoint requires EDIT permission"""
    from fastapi import HTTPException

    from registry.api.v1.server.server_routes import refresh_server_capabilities

    server_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    # Simulate permission denied
    mock_acl_service.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))

    mock_server_service = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await refresh_server_capabilities(
            server_id=server_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

    # Verify permission check raised 403
    assert exc_info.value.status_code == 403

    # Verify service was never called (permission check failed first)
    mock_server_service.refresh_server_capabilities.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_server_capabilities_server_not_found():
    """Test refresh endpoint returns 404 when server not found"""
    from fastapi import HTTPException

    from registry.api.v1.server.server_routes import refresh_server_capabilities

    server_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())

    mock_server_service = MagicMock()
    mock_server_service.refresh_server_capabilities = AsyncMock(side_effect=ValueError("Server not found"))

    with pytest.raises(HTTPException) as exc_info:
        await refresh_server_capabilities(
            server_id=server_id,
            user_context=user_context,
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

    # Verify 404 error
    assert exc_info.value.status_code == 404
    assert "not_found" in str(exc_info.value.detail)


def _fake_mcp_server(*, enabled: bool = True):
    """Minimal ExtendedMCPServer stand-in for converter tests."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=PydanticObjectId(),
        serverName="test-server",
        author=None,
        numTools=2,
        numStars=0,
        path="/test",
        tags=["t"],
        lastConnected=None,
        lastError=None,
        createdAt=now,
        updatedAt=now,
        config={"enabled": enabled, "title": "Test", "description": "desc"},
    )


def test_convert_to_list_item_omits_status_and_reads_config_enabled():
    server = _fake_mcp_server(enabled=True)
    item = convert_to_list_item(server)
    assert not hasattr(item, "status")
    # enablement comes from config.enabled, not status
    assert item.enabled is True


def test_convert_to_list_item_reflects_disabled_config():
    server = _fake_mcp_server(enabled=False)
    item = convert_to_list_item(server)
    assert not hasattr(item, "status")
    assert item.enabled is False


def test_convert_to_detail_omits_status():
    server = _fake_mcp_server(enabled=True)
    detail = convert_to_detail(server)
    assert not hasattr(detail, "status")
    assert detail.enabled is True


@pytest.mark.asyncio
async def test_list_servers_route_requests_all_servers_with_enabled_only_false(sample_user_context):
    """The list endpoint dropped ?status and must ask the service for all servers."""
    from registry.api.v1.server.server_routes import list_servers as list_servers_route

    server = _fake_mcp_server(enabled=True)

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[str(server.id)])
    acl_service.get_user_permissions_for_resource = AsyncMock(return_value=None)

    server_service = MagicMock()
    server_service.list_servers = AsyncMock(return_value=([server], 1))

    with patch(
        "registry.api.v1.server.server_routes.get_servers_connection_status",
        new=AsyncMock(return_value={}),
    ):
        result = await list_servers_route(
            query=None,
            page=1,
            per_page=20,
            user_context=sample_user_context,
            acl_service=acl_service,
            server_service=server_service,
            mcp_service=MagicMock(),
            status_resolver=MagicMock(),
        )

    assert server_service.list_servers.await_args.kwargs["enabled_only"] is False
    assert result.pagination.total == 1
    assert len(result.servers) == 1
    assert not hasattr(result.servers[0], "status")


@pytest.mark.asyncio
async def test_list_servers_maps_acl_runtime_error_to_503(sample_user_context):
    """An ACL/DB outage (RuntimeError from get_accessible_resource_ids) must surface as 503, not 500."""
    from fastapi import HTTPException

    from registry.api.v1.server.server_routes import list_servers as list_servers_route

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(
        side_effect=RuntimeError("Failed to fetch accessible resources")
    )

    with pytest.raises(HTTPException) as exc_info:
        await list_servers_route(
            query=None,
            page=1,
            per_page=20,
            user_context=sample_user_context,
            acl_service=acl_service,
            server_service=MagicMock(),
            mcp_service=MagicMock(),
            status_resolver=MagicMock(),
        )

    assert exc_info.value.status_code == 503
