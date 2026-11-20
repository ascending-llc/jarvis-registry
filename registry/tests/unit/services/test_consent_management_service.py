import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from mcp.types import ClientCapabilities, Implementation, InitializeRequestParams

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
