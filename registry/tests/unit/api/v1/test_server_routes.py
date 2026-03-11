from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.api.v1.server.server_routes import create_server
from registry.schemas.server_api_schemas import ServerCreateRequest
from registry_pkgs.models._generated import PrincipalType, ResourceType
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

    with (
        patch(
            "registry.api.v1.server.server_routes.server_service_v1.create_server",
            new=AsyncMock(return_value=mock_created_server),
        ) as mock_create_server,
        patch(
            "registry.api.v1.server.server_routes.acl_service.grant_permission", new=AsyncMock(return_value=MagicMock())
        ) as mock_grant_permission,
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
            sample_server_request,
            sample_user_context,
        )

        # Verify server creation was called correctly
        mock_create_server.assert_awaited_once_with(
            data=sample_server_request,
            user_id=sample_user_context["user_id"],
        )

        # Verify ACL permission was granted
        mock_grant_permission.assert_awaited_once()

        # Verify ACL call has correct parameters
        call_args = mock_grant_permission.call_args
        assert call_args.kwargs["principal_type"] == PrincipalType.USER
        assert call_args.kwargs["principal_id"] == PydanticObjectId(sample_user_context["user_id"])
        assert call_args.kwargs["resource_type"] == ResourceType.MCPSERVER
        assert call_args.kwargs["resource_id"] == mock_created_server.id
        assert call_args.kwargs["perm_bits"] == RoleBits.OWNER
