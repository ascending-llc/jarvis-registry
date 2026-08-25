"""Tests for workflow OAuth re-authorization preflight checks."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.services.workflow_reauth_service import collect_pending_oauth_authorizations
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode


def _step(name: str, executor_key: str) -> WorkflowNode:
    return WorkflowNode(name=name, executor_key=executor_key, step_objective=f"Run {name}")


def _workflow(*nodes: WorkflowNode) -> WorkflowDefinition:
    return WorkflowDefinition.model_construct(name="workflow", nodes=list(nodes))


def _server(name: str, config: dict) -> ExtendedMCPServer:
    return ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName=name,
        config={"enabled": True, **config},
        author=PydanticObjectId(),
    )


def _oauth_service() -> MagicMock:
    service = MagicMock()
    service.get_valid_access_token = AsyncMock()
    service.flow_manager.generate_flow_id.side_effect = lambda user_id, server_id: f"{user_id}:{server_id}"
    return service


@pytest.mark.asyncio
async def test_collect_pending_authorizations_skips_query_without_executor_keys() -> None:
    workflow = _workflow(
        WorkflowNode(
            name="pool",
            a2a_pool=["agent-a"],
            step_objective="Delegate",
        )
    )

    with patch("registry.services.workflow_reauth_service.ExtendedMCPServer.find") as find:
        result = await collect_pending_oauth_authorizations(
            workflow,
            user_id="user-1",
            oauth_service=_oauth_service(),
        )

    assert result == []
    find.assert_not_called()


@pytest.mark.asyncio
async def test_collect_pending_authorizations_filters_auth_modes_and_batches_query() -> None:
    oauth_valid = _server("oauth-valid", {"oauth": {"client_id": "valid"}})
    oauth_pending = _server("oauth-pending", {"requiresOAuth": True})
    servers = [
        oauth_valid,
        oauth_pending,
        _server("agentcore", {"runtimeAccess": {"mode": "jwt"}}),
        _server("api-key", {"apiKey": {"key": "secret"}}),
        _server("none", {}),
    ]
    workflow = _workflow(
        _step("valid", "oauth-valid"),
        _step("pending", "oauth-pending"),
        _step("duplicate", "oauth-pending"),
        _step("a2a-or-builtin", "not-an-mcp"),
    )
    oauth_service = _oauth_service()
    oauth_service.get_valid_access_token.side_effect = [
        ("access-token", None, None),
        (None, "https://issuer.example/authorize", None),
    ]
    query = MagicMock()
    query.to_list = AsyncMock(return_value=servers)

    with patch(
        "registry.services.workflow_reauth_service.ExtendedMCPServer.find",
        return_value=query,
    ) as find:
        result = await collect_pending_oauth_authorizations(
            workflow,
            user_id="user-1",
            oauth_service=oauth_service,
        )

    find.assert_called_once()
    query.to_list.assert_awaited_once_with()
    assert oauth_service.get_valid_access_token.await_count == 2
    assert oauth_service.get_valid_access_token.await_args_list[0].kwargs["server"] is oauth_valid
    assert oauth_service.get_valid_access_token.await_args_list[1].kwargs["server"] is oauth_pending
    assert len(result) == 1
    assert result[0].serverId == str(oauth_pending.id)
    assert result[0].serverName == "oauth-pending"
    assert result[0].authUrl == "https://issuer.example/authorize"
    assert result[0].flowId == f"user-1:{oauth_pending.id}"


@pytest.mark.asyncio
async def test_collect_pending_authorizations_maps_oauth_error_to_400() -> None:
    server = _server("broken-oauth", {"oauth": {}})
    oauth_service = _oauth_service()
    oauth_service.get_valid_access_token.return_value = (None, None, "discovery failed")
    query = MagicMock()
    query.to_list = AsyncMock(return_value=[server])

    with (
        patch(
            "registry.services.workflow_reauth_service.ExtendedMCPServer.find",
            return_value=query,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await collect_pending_oauth_authorizations(
            _workflow(_step("broken", "broken-oauth")),
            user_id="user-1",
            oauth_service=oauth_service,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "error": "invalid_request",
        "message": "OAuth token error for server 'broken-oauth': discovery failed",
    }
