import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from registry.api.v1.server import server_routes
from registry.api.v1.server.server_routes import create_server, delete_server, update_server
from registry.schemas.server_api_schemas import (
    ServerCreateRequest,
    ServerUpdateRequest,
    convert_to_detail,
    convert_to_list_item,
)
from registry.services.background_task_tracker import BackgroundTaskTracker
from registry.services.server_service import ServerServiceV1
from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import RoleBits
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer


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
    events: list[str] = []

    def record_create(**_: object) -> MagicMock:
        events.append("create")
        return mock_created_server

    def record_response(*_: object, **__: object) -> dict[str, str]:
        events.append("response")
        return {"id": str(mock_created_server.id)}

    # Mock the transaction session
    mock_session = AsyncMock()
    mock_server_service = MagicMock()
    mock_server_service.create_server = AsyncMock(side_effect=record_create)
    mock_server_service.schedule_registration_capabilities = MagicMock(side_effect=lambda *_: events.append("schedule"))
    mock_acl_service = MagicMock()
    mock_acl_service.grant_permission = AsyncMock(side_effect=lambda **_: events.append("grant"))

    with (
        patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client,
        patch(
            "registry.api.v1.server.server_routes.convert_to_detail",
            side_effect=record_response,
        ),
    ):
        # Mock the MongoDB client and session for the explicit transaction block
        mock_client = MagicMock()
        mock_client.start_session.return_value.__aenter__.return_value = mock_session
        mock_client.start_session.return_value.__aexit__.side_effect = lambda *_: events.append("session_exit")
        mock_session.start_transaction.return_value.__aenter__.return_value = None
        mock_session.start_transaction.return_value.__aexit__.side_effect = lambda *_: events.append("transaction_exit")
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

        mock_server_service.schedule_registration_capabilities.assert_called_once_with(
            mock_created_server, sample_user_context["user_id"]
        )
        assert events == ["create", "grant", "transaction_exit", "session_exit", "schedule", "response"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["create", "grant", "commit", "session-exit"])
async def test_create_server_route_skips_capabilities_when_transaction_fails(
    sample_server_request: ServerCreateRequest,
    sample_user_context: dict[str, object],
    mock_created_server: MagicMock,
    failure: str,
) -> None:
    service = MagicMock()
    service.create_server = AsyncMock(return_value=mock_created_server)
    service.schedule_registration_capabilities = MagicMock()
    acl = MagicMock()
    acl.grant_permission = AsyncMock()

    with (
        patch("registry.api.v1.server.server_routes.MongoDB.get_client") as get_client,
        patch("registry.api.v1.server.server_routes.convert_to_detail") as convert,
    ):
        session = _mock_mongo_transaction(get_client)
        failure_points = {
            "create": service.create_server,
            "grant": acl.grant_permission,
            "commit": session.start_transaction.return_value.__aexit__,
            "session-exit": get_client.return_value.start_session.return_value.__aexit__,
        }
        failure_points[failure].side_effect = RuntimeError("registration failed")

        with pytest.raises(HTTPException) as exc_info:
            await create_server(
                data=sample_server_request,
                user_context=sample_user_context,
                server_service=service,
                acl_service=acl,
            )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error while creating server"
    service.schedule_registration_capabilities.assert_not_called()
    convert.assert_not_called()
    if failure == "create":
        acl.grant_permission.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tracker_state", ["running", "shutting-down"])
async def test_create_server_returns_201_without_waiting_for_capabilities(
    sample_server_request: ServerCreateRequest,
    sample_user_context: dict[str, object],
    tracker_state: str,
) -> None:
    now = datetime.now(UTC)
    server = ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName="test-server",
        author=None,
        lastConnected=None,
        lastError=None,
        path="/test",
        tags=["t"],
        numTools=0,
        numStars=0,
        createdAt=now,
        updatedAt=now,
        config={"enabled": False, "title": "Test", "url": sample_server_request.url, "capabilities": "{}"},
    )
    tracker = BackgroundTaskTracker()
    if tracker_state == "shutting-down":
        await tracker.shutdown()
    service = ServerServiceV1(
        user_service=MagicMock(),
        token_service=MagicMock(),
        oauth_service=MagicMock(),
        mcp_server_repo=MagicMock(),
        background_task_tracker=tracker,
    )
    service.create_server = AsyncMock(return_value=server)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def slow_fetch(*_: object) -> None:
        await release.wait()
        finished.set()

    service.fetch_registration_capabilities = AsyncMock(side_effect=slow_fetch)
    acl = MagicMock()
    acl.grant_permission = AsyncMock()
    app = FastAPI()
    app.include_router(server_routes.router, prefix="/api/v1")
    app.dependency_overrides[server_routes.get_user_context] = lambda: sample_user_context
    app.dependency_overrides[server_routes.get_server_service] = lambda: service
    app.dependency_overrides[server_routes.get_acl_service] = lambda: acl

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as get_client:
        _mock_mongo_transaction(get_client)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/servers", json=sample_server_request.model_dump(mode="json", by_alias=True)
            )

    assert response.status_code == 201
    assert response.json()["id"] == str(server.id)
    assert response.json()["capabilities"] == "{}"
    acl.grant_permission.assert_awaited_once()
    assert not finished.is_set()
    if tracker_state == "running":
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        (copy, user_id), _ = service.fetch_registration_capabilities.call_args
        assert copy is not server
        assert copy.id == server.id
        assert user_id == sample_user_context["user_id"]
    else:
        service.fetch_registration_capabilities.assert_not_awaited()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["toggle", "refresh"])
@pytest.mark.parametrize(
    ("server_name", "expected_name"),
    [("google-workspace", "google-workspace"), (None, "this server")],
)
async def test_server_routes_map_oauth_reauth_required_to_400(route, server_name, expected_name):
    """Toggle and refresh return 400 oauth_required (never 401) with no auth URL in the body."""
    from registry.api.v1.server.server_routes import refresh_server_capabilities, toggle_server
    from registry.schemas.server_api_schemas import ServerToggleRequest
    from registry_pkgs.oauth.errors import OAuthReAuthRequiredError

    server_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}
    auth_url = "https://accounts.example.com/authorize?state=secret"

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    error = OAuthReAuthRequiredError("re-auth required", auth_url=auth_url, server_name=server_name)
    mock_server_service = MagicMock()
    mock_server_service.toggle_server_status = AsyncMock(side_effect=error)
    mock_server_service.refresh_server_capabilities = AsyncMock(side_effect=error)

    with pytest.raises(HTTPException) as exc_info:
        if route == "toggle":
            await toggle_server(
                server_id=server_id,
                data=ServerToggleRequest(enabled=True),
                user_context=user_context,
                acl_service=mock_acl_service,
                server_service=mock_server_service,
            )
        else:
            await refresh_server_capabilities(
                server_id=server_id,
                user_context=user_context,
                acl_service=mock_acl_service,
                server_service=mock_server_service,
            )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "error": "oauth_required",
        "message": f"Authorization required: connect your account to '{expected_name}' first.",
    }
    assert "http" not in str(exc_info.value.detail)


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
async def test_get_server_oauth_fallback_reads_requires_oauth_on_status_error(sample_user_context):
    """When the connection-status lookup raises, the fallback reads config.requiresOAuth (camelCase)."""
    from registry.api.v1.server.server_routes import get_server as get_server_route

    server = _fake_mcp_server(enabled=True)
    server.config["requiresOAuth"] = True

    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock(return_value=None)
    server_service = MagicMock()
    server_service.get_server_by_id = AsyncMock(return_value=server)

    with patch(
        "registry.api.v1.server.server_routes.get_single_server_connection_status",
        new=AsyncMock(side_effect=RuntimeError("status backend down")),
    ):
        detail = await get_server_route(
            server_id=str(server.id),
            user_context=sample_user_context,
            acl_service=acl_service,
            server_service=server_service,
            mcp_service=MagicMock(),
            status_resolver=MagicMock(),
        )

    assert detail.requiresOauth is True


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


@pytest.mark.asyncio
async def test_list_servers_maps_per_item_permission_runtime_error_to_503(sample_user_context):
    """A per-server permission lookup outage must surface as 503, not an empty ACL permission."""
    from fastapi import HTTPException

    from registry.api.v1.server.server_routes import list_servers as list_servers_route

    server = _fake_mcp_server(enabled=True)
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[str(server.id)])
    acl_service.get_user_permissions_for_resource = AsyncMock(side_effect=RuntimeError("acl unavailable"))

    server_service = MagicMock()
    server_service.list_servers = AsyncMock(return_value=([server], 1))

    with pytest.raises(HTTPException) as exc_info:
        await list_servers_route(
            query=None,
            page=1,
            per_page=20,
            user_context=sample_user_context,
            acl_service=acl_service,
            server_service=server_service,
            mcp_service=MagicMock(),
            status_resolver=MagicMock(),
        )

    assert exc_info.value.status_code == 503


def _mock_mongo_transaction(mock_get_client: MagicMock) -> AsyncMock:
    mock_session = AsyncMock()
    mock_client = MagicMock()
    mock_client.start_session.return_value.__aenter__.return_value = mock_session
    mock_client.start_session.return_value.__aexit__.return_value = False
    mock_session.start_transaction.return_value.__aenter__.return_value = None
    mock_session.start_transaction.return_value.__aexit__.return_value = False
    mock_get_client.return_value = mock_client
    return mock_session


@pytest.mark.asyncio
async def test_create_server_route_returns_503_during_reindex(sample_server_request, sample_user_context):
    mock_server_service = MagicMock()
    mock_server_service.create_server = AsyncMock(side_effect=EmbeddingReindexInProgressException("reindex"))
    mock_server_service.schedule_registration_capabilities = MagicMock()

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client:
        _mock_mongo_transaction(mock_get_client)
        with pytest.raises(HTTPException) as excinfo:
            await create_server(
                data=sample_server_request,
                user_context=sample_user_context,
                server_service=mock_server_service,
                acl_service=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 503
    assert exc.headers["Retry-After"] == "30"
    assert exc.detail["error"] == "reindex_in_progress"
    mock_server_service.schedule_registration_capabilities.assert_not_called()


@pytest.mark.asyncio
async def test_update_server_route_returns_503_during_reindex(sample_user_context):
    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    mock_server_service = MagicMock()
    mock_server_service.update_server = AsyncMock(side_effect=EmbeddingReindexInProgressException("reindex"))

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client:
        _mock_mongo_transaction(mock_get_client)
        with pytest.raises(HTTPException) as excinfo:
            await update_server(
                server_id=str(PydanticObjectId()),
                data=ServerUpdateRequest(),
                user_context=sample_user_context,
                acl_service=mock_acl_service,
                server_service=mock_server_service,
            )

    exc = excinfo.value
    assert exc.status_code == 503
    assert exc.headers["Retry-After"] == "30"
    assert exc.detail["error"] == "reindex_in_progress"


@pytest.mark.asyncio
async def test_update_server_route_schedules_vector_sync_after_commit(sample_user_context):
    """The route schedules the returned plan's vector sync only after the transaction block exits."""
    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    plan = MagicMock()
    mock_server_service = MagicMock()
    mock_server_service.update_server = AsyncMock(return_value=(_fake_mcp_server(enabled=True), plan))

    with (
        patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client,
        patch("registry.api.v1.server.server_routes.convert_to_detail", return_value="ok"),
    ):
        _mock_mongo_transaction(mock_get_client)
        result = await update_server(
            server_id=str(PydanticObjectId()),
            data=ServerUpdateRequest(),
            user_context=sample_user_context,
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

    assert result == "ok"
    mock_server_service.schedule_vector_sync.assert_called_once_with(plan)


@pytest.mark.asyncio
async def test_update_server_route_does_not_schedule_when_transaction_raises(sample_user_context):
    """A failure inside the transaction must leave the vector sync unscheduled (no stale write)."""
    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(side_effect=RuntimeError("acl boom"))
    mock_server_service = MagicMock()

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client:
        _mock_mongo_transaction(mock_get_client)
        with pytest.raises((HTTPException, RuntimeError)):
            await update_server(
                server_id=str(PydanticObjectId()),
                data=ServerUpdateRequest(),
                user_context=sample_user_context,
                acl_service=mock_acl_service,
                server_service=mock_server_service,
            )

    mock_server_service.schedule_vector_sync.assert_not_called()


@pytest.mark.asyncio
async def test_delete_server_route_returns_503_during_reindex(sample_user_context):
    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    mock_server_service = MagicMock()
    mock_server_service.delete_server = AsyncMock(side_effect=EmbeddingReindexInProgressException("reindex"))

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client:
        _mock_mongo_transaction(mock_get_client)
        with pytest.raises(HTTPException) as excinfo:
            await delete_server(
                server_id=str(PydanticObjectId()),
                user_context=sample_user_context,
                acl_service=mock_acl_service,
                server_service=mock_server_service,
            )

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error"] == "reindex_in_progress"
    # delete_server raised, so the block never committed: no vector delete is scheduled.
    mock_server_service.schedule_vector_delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_server_route_schedules_vector_delete_after_commit(sample_user_context):
    """The route removes vectors only after the delete transaction commits, using the returned name."""
    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    mock_acl_service.delete_acl_entries_for_resource = AsyncMock(return_value=1)
    mock_server_service = MagicMock()
    mock_server_service.delete_server = AsyncMock(return_value="srv-x")
    server_id = str(PydanticObjectId())

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client:
        _mock_mongo_transaction(mock_get_client)
        result = await delete_server(
            server_id=server_id,
            user_context=sample_user_context,
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

    assert result is None
    mock_server_service.schedule_vector_delete.assert_called_once_with(server_id, "srv-x")


@pytest.mark.asyncio
async def test_delete_server_route_does_not_schedule_when_acl_cleanup_fails(sample_user_context):
    """A failure inside the transaction (ACL cleanup) must leave vectors in place (not scheduled)."""
    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    mock_acl_service.delete_acl_entries_for_resource = AsyncMock(side_effect=RuntimeError("acl boom"))
    mock_server_service = MagicMock()
    mock_server_service.delete_server = AsyncMock(return_value="srv-x")

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client:
        _mock_mongo_transaction(mock_get_client)
        with pytest.raises(HTTPException) as excinfo:
            await delete_server(
                server_id=str(PydanticObjectId()),
                user_context=sample_user_context,
                acl_service=mock_acl_service,
                server_service=mock_server_service,
            )

    assert excinfo.value.status_code == 500
    mock_server_service.schedule_vector_delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_server_route_does_not_schedule_when_commit_fails(sample_user_context):
    """The block succeeds but the commit raises: the delete rolled back, so vectors stay in place."""
    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    mock_acl_service.delete_acl_entries_for_resource = AsyncMock(return_value=1)
    mock_server_service = MagicMock()
    mock_server_service.delete_server = AsyncMock(return_value="srv-x")

    with patch("registry.api.v1.server.server_routes.MongoDB.get_client") as mock_get_client:
        _mock_mongo_transaction(mock_get_client)
        txn = mock_get_client.return_value.start_session.return_value.__aenter__.return_value
        txn.start_transaction.return_value.__aexit__.side_effect = RuntimeError("commit failed")
        with pytest.raises(HTTPException) as excinfo:
            await delete_server(
                server_id=str(PydanticObjectId()),
                user_context=sample_user_context,
                acl_service=mock_acl_service,
                server_service=mock_server_service,
            )

    assert excinfo.value.status_code == 500
    # Every statement in the block ran; only the commit failed.
    mock_acl_service.delete_acl_entries_for_resource.assert_awaited_once()
    mock_server_service.schedule_vector_delete.assert_not_called()


def test_convert_to_detail_includes_disabled_tools():
    server = _fake_mcp_server(enabled=True)
    server.registryDisabledTools = ["delete_repo", "force_push"]
    detail = convert_to_detail(server)
    assert detail.disabledTools == ["delete_repo", "force_push"]


def test_convert_to_detail_disabled_tools_defaults_empty():
    """A server without the field (or None) yields an empty list, never an error."""
    server = _fake_mcp_server(enabled=True)  # no registryDisabledTools attribute
    detail = convert_to_detail(server)
    assert detail.disabledTools == []


@pytest.mark.asyncio
async def test_update_server_tools_owner_success():
    """OWNER (SHARE) can replace the disabled-tools list; service is called with the submitted list."""
    from registry.api.v1.server.server_routes import update_server_tools
    from registry.schemas.server_api_schemas import ServerToolsUpdateRequest

    server_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=None)

    updated_server = _fake_mcp_server(enabled=True)
    updated_server.registryDisabledTools = ["delete_repo"]
    mock_server_service = MagicMock()
    mock_server_service.update_disabled_tools = AsyncMock(return_value=updated_server)

    result = await update_server_tools(
        server_id=server_id,
        data=ServerToolsUpdateRequest(disabledTools=["delete_repo"]),
        user_context=user_context,
        acl_service=mock_acl_service,
        server_service=mock_server_service,
    )

    # Gated on SHARE (OWNER-only).
    assert mock_acl_service.check_user_permission.call_args.kwargs["required_permission"] == "SHARE"
    mock_server_service.update_disabled_tools.assert_awaited_once()
    assert mock_server_service.update_disabled_tools.call_args.kwargs["disabled_tools"] == ["delete_repo"]
    assert result.disabledTools == ["delete_repo"]


@pytest.mark.asyncio
async def test_update_server_tools_non_owner_forbidden():
    """A VIEWER/EDITOR (lacking SHARE) gets 403 and the service is never called."""
    from fastapi import HTTPException

    from registry.api.v1.server.server_routes import update_server_tools
    from registry.schemas.server_api_schemas import ServerToolsUpdateRequest

    server_id = str(PydanticObjectId())
    user_context = {"user_id": str(PydanticObjectId())}

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))

    mock_server_service = MagicMock()
    mock_server_service.update_disabled_tools = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await update_server_tools(
            server_id=server_id,
            data=ServerToolsUpdateRequest(disabledTools=["x"]),
            user_context=user_context,
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

    assert exc_info.value.status_code == 403
    mock_server_service.update_disabled_tools.assert_not_called()


@pytest.mark.asyncio
async def test_update_server_tools_server_not_found():
    from fastapi import HTTPException

    from registry.api.v1.server.server_routes import update_server_tools
    from registry.schemas.server_api_schemas import ServerToolsUpdateRequest

    mock_acl_service = MagicMock()
    mock_acl_service.check_user_permission = AsyncMock(return_value=MagicMock())
    mock_server_service = MagicMock()
    mock_server_service.update_disabled_tools = AsyncMock(side_effect=ValueError("Server not found"))

    with pytest.raises(HTTPException) as exc_info:
        await update_server_tools(
            server_id=str(PydanticObjectId()),
            data=ServerToolsUpdateRequest(disabledTools=["x"]),
            user_context={"user_id": str(PydanticObjectId())},
            acl_service=mock_acl_service,
            server_service=mock_server_service,
        )

    assert exc_info.value.status_code == 404
    assert "not_found" in str(exc_info.value.detail)
