import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp.types import ClientCapabilities, Implementation, InitializeRequestParams
from redis.exceptions import RedisError

from registry.core.config import settings
from registry.mcpgw.core.client_info_store import ClientInfoStore
from registry.services.a2a_agent_service import A2AAgentService
from registry.services.consent_management_service import ConsentManagementService
from registry.services.server_service import ServerServiceV1
from registry_pkgs.core.client_categories import CLAUDE_DESKTOP_CLIENT_ID, REGISTRY_CLI_CLIENT_ID, ClientCategory
from registry_pkgs.core.config import INTERACTIVE_TOKEN_CLIENT_ID
from registry_pkgs.core.consent_store import ConsentEntry, ConsentEntryKind, ConsentStore
from registry_pkgs.core.oauth_state_store import OAuthClientStore

USER_ID = "507f1f77bcf86cd799439011"
APP = ConsentEntry(ConsentEntryKind.APP, "")


@pytest.fixture
def stores() -> tuple[Mock, Mock, Mock]:
    consents, registrations, info = Mock(spec=ConsentStore), Mock(spec=OAuthClientStore), Mock(spec=ClientInfoStore)
    registrations.get_clients_without_ttl_slide.return_value = {}
    info.load_many_for_dcr.return_value = {}
    return consents, registrations, info


@pytest.fixture
def name_services() -> tuple[Mock, Mock]:
    servers, agents = Mock(spec=ServerServiceV1), Mock(spec=A2AAgentService)
    servers.get_names_by_paths.return_value = {}
    agents.get_names_by_paths.return_value = {}
    return servers, agents


@pytest.fixture
def service(stores: tuple[Mock, Mock, Mock], name_services: tuple[Mock, Mock]) -> ConsentManagementService:
    return ConsentManagementService(*stores, settings.jwt_token_config, *name_services)


async def test_categories_and_registration_metadata(
    service: ConsentManagementService, stores: tuple[Mock, Mock, Mock]
) -> None:
    consents, registrations, info = stores
    entries = {
        "mcp-client-expired": [APP],
        "mcp-client-desktop": [APP],
        "mcp-client-new-protocol": [APP],
        "a2a-client-agent": [APP],
        CLAUDE_DESKTOP_CLIENT_ID: [APP],
        REGISTRY_CLI_CLIENT_ID: [APP],
        INTERACTIVE_TOKEN_CLIENT_ID: [APP, ConsentEntry(ConsentEntryKind.UNKNOWN, "old-field")],
        "legacy": [APP],
    }
    consents.list_client_ids.return_value = [
        *entries,
        settings.jwt_token_config.registry_client_id,
        settings.jwt_token_config.headless_agent_client_id,
        "mcp-client-vanished",
    ]
    consents.get_entries.return_value = entries
    registrations.get_clients_without_ttl_slide.return_value = {
        "mcp-client-expired": None,
        "mcp-client-desktop": {
            "client_name": "alpha",
            "client_uri": "https://example.org",
            "redirect_uris": ["https://example.org/callback"],
            "registered_at": 1234567890,
            "ip_address": "192.0.2.1",
            "client_secret_hash": "private",
        },
        "a2a-client-agent": {"client_name": "Alpha"},
        "mcp-client-new-protocol": {"client_name": "New protocol"},
    }
    info.load_many_for_dcr.return_value = {
        "mcp-client-desktop": InitializeRequestParams(
            protocolVersion="2025-11-25",
            capabilities=ClientCapabilities(),
            clientInfo=Implementation(name="editor", version="2.0"),
        )
    }

    with patch("asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)) as to_thread:
        result = await service.list_clients(USER_ID)

    to_thread.assert_awaited_once()
    consents.list_client_ids.assert_called_once_with(USER_ID)
    consents.get_entries.assert_called_once_with(USER_ID, [*entries, "mcp-client-vanished"])
    registrations.get_clients_without_ttl_slide.assert_called_once_with(
        ["mcp-client-expired", "mcp-client-desktop", "mcp-client-new-protocol", "a2a-client-agent"]
    )
    info.load_many_for_dcr.assert_called_once_with(
        USER_ID, ["mcp-client-expired", "mcp-client-desktop", "mcp-client-new-protocol"]
    )
    clients = {client.clientId: client for client in result.clients}
    assert list(clients) == [
        "a2a-client-agent",
        "mcp-client-desktop",
        CLAUDE_DESKTOP_CLIENT_ID,
        INTERACTIVE_TOKEN_CLIENT_ID,
        REGISTRY_CLI_CLIENT_ID,
        "legacy",
        "mcp-client-expired",
        "mcp-client-new-protocol",
    ]
    assert clients["mcp-client-desktop"].model_dump() == {
        "clientId": "mcp-client-desktop",
        "category": ClientCategory.MCP_DCR,
        "displayName": "alpha",
        "appApproved": True,
        "registrationExpired": False,
        "sharedAcrossDevices": False,
        "revokesTokens": True,
        "clientUri": "https://example.org",
        "redirectUris": ["https://example.org/callback"],
        "registeredAt": 1234567890,
        "registrationIp": "192.0.2.1",
        "softwareName": "editor",
        "softwareVersion": "2.0",
        "resources": [],
    }
    expired = clients["mcp-client-expired"]
    assert expired.registrationExpired and expired.displayName == expired.clientId and expired.appApproved
    current = clients["mcp-client-new-protocol"]
    assert not current.registrationExpired and current.softwareName is None and current.softwareVersion is None
    for cid, name in ((CLAUDE_DESKTOP_CLIENT_ID, "Claude Desktop"), (REGISTRY_CLI_CLIENT_ID, "Jarvis Registry CLI")):
        assert clients[cid].displayName == name and clients[cid].sharedAcrossDevices
    interactive = clients[INTERACTIVE_TOKEN_CLIENT_ID]
    assert (
        interactive.displayName == "Interactive tokens"
        and not interactive.appApproved
        and not interactive.revokesTokens
    )
    assert [r.path for r in interactive.resources] == ["old-field"]
    assert clients["legacy"].category is ClientCategory.UNKNOWN and clients["legacy"].displayName == "legacy"


async def test_resources_are_batched_across_clients(
    service: ConsentManagementService, stores: tuple[Mock, Mock, Mock], name_services: tuple[Mock, Mock]
) -> None:
    consents, _, _ = stores
    servers, agents = name_services
    known_server = ConsentEntry(ConsentEntryKind.MCP_SERVER, "/live")
    known_agent = ConsentEntry(ConsentEntryKind.A2A_AGENT, "research")
    consents.list_client_ids.return_value = ["old-app", "other-app"]
    consents.get_entries.return_value = {
        "old-app": [
            known_server,
            ConsentEntry(ConsentEntryKind.UNKNOWN, "research"),
            known_agent,
            ConsentEntry(ConsentEntryKind.MCP_SERVER, "/deleted"),
        ],
        "other-app": [known_server, known_agent, APP],
    }
    servers.get_names_by_paths.return_value = {"/live": "Live server"}
    agents.get_names_by_paths.return_value = {"research": "Research"}

    old, other = (await service.list_clients(USER_ID)).clients

    servers.get_names_by_paths.assert_awaited_once_with({"/live", "/deleted"})
    agents.get_names_by_paths.assert_awaited_once_with({"research"})
    assert not old.appApproved and other.appApproved
    assert [(r.type, r.path, r.name) for r in old.resources] == [
        ("a2a_agent", "research", "Research"),
        ("mcp_server", "/deleted", None),
        ("mcp_server", "/live", "Live server"),
        ("unknown", "research", None),
    ]
    assert [r.name for r in other.resources] == ["Research", "Live server"]


@pytest.mark.parametrize(
    ("client_uri", "expected"),
    [
        ("https://example.org/app", "https://example.org/app"),
        ("HTTP://example.org", "HTTP://example.org"),
        ("javascript:alert(1)", None),
        (" javascript:alert(1)", None),
        ("java\tscript:alert(1)", None),
        ("data:text/html,<script>alert(1)</script>", None),
        ("https:no-host", None),
        ("//example.org", None),
        ("http://[invalid", None),
        (123, None),
        (None, None),
    ],
)
async def test_client_uri_is_returned_only_for_http_urls(
    service: ConsentManagementService, stores: tuple[Mock, Mock, Mock], client_uri: object, expected: str | None
) -> None:
    consents, registrations, _ = stores
    consents.list_client_ids.return_value = ["mcp-client-a"]
    consents.get_entries.return_value = {"mcp-client-a": [APP]}
    registrations.get_clients_without_ttl_slide.return_value = {
        "mcp-client-a": {"client_name": "A", "client_uri": client_uri}
    }

    (client,) = (await service.list_clients(USER_ID)).clients

    assert client.clientUri == expected


@pytest.mark.parametrize("revoke_all", [True, False])
async def test_batch_revocation_uses_one_worker_and_one_cache_pass(
    service: ConsentManagementService, stores: tuple[Mock, Mock, Mock], revoke_all: bool
) -> None:
    consents, registrations, info = stores
    headless = settings.jwt_token_config.headless_agent_client_id
    ids = ["mcp-client-a", headless, "legacy", "mcp-client-a"]
    consents.list_client_ids.return_value = sorted(set(ids))
    consents.delete_clients.return_value = {"mcp-client-a": True, "legacy": False}
    info.delete_for_clients.return_value = 3

    with patch("asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)) as worker:
        results = await service.revoke_clients(USER_ID, None if revoke_all else ids, revoke_all)

    worker.assert_awaited_once()
    info.delete_for_clients.assert_called_once_with(USER_ID, {"legacy", "mcp-client-a"})
    if revoke_all:
        consents.list_client_ids.assert_called_once_with(USER_ID)
        consents.delete_clients.assert_called_once_with(USER_ID, ["legacy", "mcp-client-a"])
        assert [(r.clientId, r.status) for r in results] == [("legacy", "not_found"), ("mcp-client-a", "revoked")]
    else:
        consents.list_client_ids.assert_not_called()
        consents.delete_clients.assert_called_once_with(USER_ID, ["mcp-client-a", "legacy"])
        assert [r.clientId for r in results] == ids
        assert [r.status for r in results] == ["revoked", "rejected", "not_found", "revoked"]
    assert not registrations.mock_calls


async def test_revoke_all_can_retry_after_cache_failure(
    service: ConsentManagementService, stores: tuple[Mock, Mock, Mock]
) -> None:
    consents, _, info = stores
    consents.list_client_ids.return_value = ["mcp-client-a"]
    info.delete_for_clients.side_effect = RedisError("cache unavailable")
    with pytest.raises(RedisError):
        await service.revoke_clients(USER_ID, None, True)
    consents.delete_clients.assert_not_called()

    info.delete_for_clients.side_effect = None
    info.delete_for_clients.return_value = 0
    consents.delete_clients.return_value = {"mcp-client-a": True}
    (result,) = await service.revoke_clients(USER_ID, None, True)
    assert result.status == "revoked"


async def test_explicit_revoke_cleans_orphaned_cache(
    service: ConsentManagementService, stores: tuple[Mock, Mock, Mock]
) -> None:
    consents, _, info = stores
    consents.delete_clients.return_value = {"old": False}
    info.delete_for_clients.return_value = 1
    with patch("asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)) as worker:
        await service.revoke_client(USER_ID, "old")
    worker.assert_awaited_once()
    info.delete_for_clients.assert_called_once_with(USER_ID, {"old"})
    consents.delete_clients.assert_called_once_with(USER_ID, ["old"])


async def test_resource_revoke_preserves_order_and_duplicates(
    service: ConsentManagementService, stores: tuple[Mock, Mock, Mock]
) -> None:
    consents, _, info = stores
    server = ConsentEntry(ConsentEntryKind.MCP_SERVER, "/shared")
    agent = ConsentEntry(ConsentEntryKind.A2A_AGENT, "/shared")
    entries = [server, agent, server]
    consents.delete_entries.return_value = {server: True, agent: False}
    with patch("asyncio.to_thread", new=AsyncMock(wraps=asyncio.to_thread)) as worker:
        results = await service.revoke_resources(USER_ID, "old", entries)
    worker.assert_awaited_once()
    consents.delete_entries.assert_called_once_with(USER_ID, "old", entries)
    info.delete_for_clients.assert_not_called()
    assert [(r.type, r.path, r.status) for r in results] == [
        ("mcp_server", "/shared", "revoked"),
        ("a2a_agent", "/shared", "not_found"),
        ("mcp_server", "/shared", "revoked"),
    ]
