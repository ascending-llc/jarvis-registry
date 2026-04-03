from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.api.v1.server.server_routes import create_server
from registry.schemas.server_api_schemas import ServerCreateRequest
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
        patch("registry_pkgs.database.decorators.MongoDB.get_client") as mock_get_client,
        patch(
            "registry.api.v1.server.server_routes.convert_to_detail",
            return_value={"id": str(mock_created_server.id)},
        ),
    ):
        # Mock the MongoDB client and session for @use_transaction
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
