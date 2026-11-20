"""List the current user's OAuth client and resource approvals."""

import asyncio
from operator import attrgetter
from typing import Any

from mcp.types import InitializeRequestParams

from registry.mcpgw.core.client_info_store import ClientInfoStore
from registry.schemas.consent_api_schemas import ConsentClientListResponse, ConsentClientOutput, ConsentResourceOutput
from registry.services.a2a_agent_service import A2AAgentService
from registry.services.server_service import ServerServiceV1
from registry_pkgs.core.client_categories import STATIC_CLIENT_METADATA, ClientCategory, resolve_client_category
from registry_pkgs.core.config import JwtTokenConfig
from registry_pkgs.core.consent_store import ConsentEntry, ConsentEntryKind, ConsentStore
from registry_pkgs.core.oauth_state_store import OAuthClientStore

_DCR_CATEGORIES = (ClientCategory.MCP_DCR, ClientCategory.A2A_DCR)


def _client_output(
    client_id: str,
    category: ClientCategory,
    entries: list[ConsentEntry],
    registration: dict[str, Any] | None,
    params: InitializeRequestParams | None,
) -> ConsentClientOutput:
    metadata = registration or {}
    static = STATIC_CLIENT_METADATA.get(client_id)
    interactive = category is ClientCategory.USER_GENERATED
    if static is not None:
        name = static["client_name"]
    elif interactive:
        name = "Interactive tokens"
    else:
        name = metadata.get("client_name") or client_id

    return ConsentClientOutput(
        clientId=client_id,
        category=category,
        displayName=name,
        appApproved=not interactive and any(entry.kind is ConsentEntryKind.APP for entry in entries),
        registrationExpired=category in _DCR_CATEGORIES and registration is None,
        sharedAcrossDevices=static is not None,
        revokesTokens=not interactive,
        clientUri=metadata.get("client_uri"),
        redirectUris=metadata.get("redirect_uris") or [],
        registeredAt=metadata.get("registered_at"),
        registrationIp=metadata.get("ip_address"),
        softwareName=params.client_info.name if params else None,
        softwareVersion=params.client_info.version if params else None,
        resources=[
            ConsentResourceOutput(type=entry.kind, path=entry.path, name=None)
            for entry in sorted(entries, key=attrgetter("kind", "path"))
            if entry.kind is not ConsentEntryKind.APP
        ],
    )


class ConsentManagementService:
    def __init__(
        self,
        consent_store: ConsentStore,
        oauth_client_store: OAuthClientStore,
        client_info_store: ClientInfoStore,
        jwt_config: JwtTokenConfig,
        server_service: ServerServiceV1,
        a2a_agent_service: A2AAgentService,
    ) -> None:
        self._consents = consent_store
        self._registrations = oauth_client_store
        self._client_info = client_info_store
        self._jwt_config = jwt_config
        self._servers = server_service
        self._agents = a2a_agent_service

    def _read_clients(self, user_id: str) -> list[ConsentClientOutput]:
        categories = {}
        for client_id in self._consents.list_client_ids(user_id):
            category = resolve_client_category(client_id, self._jwt_config)
            if category not in (ClientCategory.REGISTRY_APP, ClientCategory.HEADLESS_AGENT):
                categories[client_id] = category

        entries = self._consents.get_entries(user_id, list(categories))
        dcr_ids = [cid for cid in entries if categories[cid] in _DCR_CATEGORIES]
        registrations = self._registrations.get_clients_without_ttl_slide(dcr_ids)
        client_info = self._client_info.load_many_for_dcr(
            user_id, [cid for cid in dcr_ids if categories[cid] is ClientCategory.MCP_DCR]
        )
        return [
            _client_output(cid, categories[cid], fields, registrations.get(cid), client_info.get(cid))
            for cid, fields in entries.items()
        ]

    async def list_clients(self, user_id: str) -> ConsentClientListResponse:
        clients = await asyncio.to_thread(self._read_clients, user_id)
        resources = [resource for client in clients for resource in client.resources]
        servers, agents = await asyncio.gather(
            self._servers.get_names_by_paths({r.path for r in resources if r.type == ConsentEntryKind.MCP_SERVER}),
            self._agents.get_names_by_paths({r.path for r in resources if r.type == ConsentEntryKind.A2A_AGENT}),
        )
        names = {ConsentEntryKind.MCP_SERVER: servers, ConsentEntryKind.A2A_AGENT: agents}
        for resource in resources:
            resource.name = names.get(resource.type, {}).get(resource.path)

        clients.sort(key=lambda client: (client.displayName.casefold(), client.clientId))
        return ConsentClientListResponse(clients=clients)
