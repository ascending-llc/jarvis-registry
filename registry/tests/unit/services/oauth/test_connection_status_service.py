import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from registry.services.oauth.connection_status_service import (
    get_servers_connection_status,
    get_single_server_connection_status,
)
from registry.services.oauth.status_resolver import ConnectionStatusResolver
from registry_pkgs.models.enums import McpAuthMode
from registry_pkgs.oauth.flow_state_manager import FlowStateManager
from registry_pkgs.oauth.schemas import OAuthFlow, OAuthFlowStatus


def server(index: int, *, oauth: bool = True, enabled: bool = True):
    return SimpleNamespace(
        id=str(index),
        serverName=f"server-{index}",
        config={"enabled": enabled},
        mcp_auth_mode=McpAuthMode.OAUTH if oauth else McpAuthMode.NONE,
    )


def harness(servers):
    manager = Mock(spec=FlowStateManager)
    manager.generate_flow_id.side_effect = lambda user, server: f"{user}:{server}"
    manager.is_flow_expired.side_effect = lambda flow: time.time() - flow.created_at >= 600
    manager.get_flows.return_value = {f"u:{s.id}": None for s in servers}
    oauth = SimpleNamespace(
        token_service=SimpleNamespace(get_authorization_status=AsyncMock(return_value={})),
        has_usable_stored_authorization=lambda server, tokens: bool(tokens),
    )
    return SimpleNamespace(oauth_service=oauth), ConnectionStatusResolver(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 50, 100])
async def test_page_reads_are_batched(count) -> None:
    servers = [server(i) for i in range(count)]
    service, resolver = harness(servers)
    result = await get_servers_connection_status("u", servers, service, resolver)
    assert len(result) == count
    assert service.oauth_service.token_service.get_authorization_status.await_count == bool(count)
    assert resolver.flow_state_manager.get_flows.await_count == bool(count)
    assert all(row["connection_state"] == "disconnected" for row in result.values())


@pytest.mark.asyncio
async def test_disabled_and_non_oauth_skip_storage() -> None:
    servers = [server(0, oauth=False), server(1, enabled=False)]
    service, resolver = harness(servers)
    result = await get_servers_connection_status("u", servers, service, resolver)
    assert result["0"]["connection_state"] == "connected"
    assert result["1"]["connection_state"] == "disconnected"
    resolver.flow_state_manager.get_flows.assert_not_awaited()
    service.oauth_service.token_service.get_authorization_status.assert_not_awaited()


@pytest.mark.parametrize(
    "status,expired,cancelled,authorized,expected",
    [
        (OAuthFlowStatus.PENDING, False, False, True, "connecting"),
        (OAuthFlowStatus.PENDING, True, False, False, "error"),
        (OAuthFlowStatus.FAILED, False, False, True, "connected"),
        (OAuthFlowStatus.FAILED, False, False, False, "error"),
        (OAuthFlowStatus.FAILED, False, True, False, "disconnected"),
        (OAuthFlowStatus.COMPLETED, True, False, False, "disconnected"),
    ],
)
def test_flow_priority(status, expired, cancelled, authorized, expected) -> None:
    _, resolver = harness([])
    flow = OAuthFlow(
        flow_id="u:s",
        server_id="s",
        server_name="s",
        user_id="u",
        code_verifier="v",
        state="s",
        status=status,
        created_at=time.time() - (601 if expired else 1),
        error="User cancelled OAuth flow" if cancelled else None,
    )
    assert resolver.resolve_oauth_state(flow, authorized) == expected


@pytest.mark.asyncio
async def test_storage_failure_and_detail_agree() -> None:
    servers = [server(0), server(1, oauth=False)]
    service, resolver = harness(servers)
    resolver.flow_state_manager.get_flows.side_effect = RuntimeError("private endpoint")
    result = await get_servers_connection_status("u", servers, service, resolver)
    detail = await get_single_server_connection_status(
        "u", "0", service, SimpleNamespace(get_server_by_id=AsyncMock(return_value=servers[0])), resolver
    )
    assert detail == result["0"] == {"requires_oauth": True, "connection_state": "error"}
    assert result["1"]["connection_state"] == "connected"


@pytest.mark.asyncio
async def test_one_malformed_flow_does_not_poison_other_servers() -> None:
    from registry_pkgs.oauth.errors import OAuthFlowStorageError

    servers = [server(0), server(1)]
    service, resolver = harness(servers)
    resolver.flow_state_manager.get_flows.return_value = {"u:0": OAuthFlowStorageError("bad record"), "u:1": None}
    result = await get_servers_connection_status("u", servers, service, resolver)
    assert result["0"]["connection_state"] == "error"
    assert result["1"]["connection_state"] == "disconnected"
