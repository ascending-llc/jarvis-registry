import logging
from typing import TypedDict

from registry_pkgs.models import ExtendedMCPServer
from registry_pkgs.models.enums import McpAuthMode

from ...schemas.enums import ConnectionState
from ..server_service import ServerServiceV1
from .mcp_service import MCPService
from .status_resolver import ConnectionStatusResolver

logger = logging.getLogger(__name__)


class ServerConnectionStatus(TypedDict):
    requires_oauth: bool
    connection_state: str


async def get_servers_connection_status(
    user_id: str,
    servers: list[ExtendedMCPServer],
    mcp_service: MCPService,
    status_resolver: ConnectionStatusResolver,
) -> dict[str, ServerConnectionStatus]:
    result: dict[str, ServerConnectionStatus] = {}
    oauth_servers = []
    for server in servers:
        oauth = server.mcp_auth_mode == McpAuthMode.OAUTH
        enabled = (server.config or {}).get("enabled") is True
        result[str(server.id)] = {"requires_oauth": oauth, "connection_state": ConnectionState.DISCONNECTED.value}
        if enabled and not oauth:
            result[str(server.id)]["connection_state"] = ConnectionState.CONNECTED.value
        elif enabled:
            oauth_servers.append(server)
    if not oauth_servers:
        return result
    flow_manager = status_resolver.flow_state_manager
    flow_ids = [flow_manager.generate_flow_id(user_id, str(server.id)) for server in oauth_servers]
    try:
        tokens = await mcp_service.oauth_service.token_service.get_authorization_status(
            user_id, [s.serverName for s in oauth_servers]
        )
        flows = await flow_manager.get_flows(flow_ids)
    except Exception:
        # A failed shared read is not evidence of missing authorization.
        logger.exception("Failed to derive OAuth connection status")
        for server in oauth_servers:
            result[str(server.id)]["connection_state"] = ConnectionState.ERROR.value
        return result
    for server, flow_id in zip(oauth_servers, flow_ids, strict=True):
        try:
            flow = flows[flow_id]
            if isinstance(flow, Exception):
                raise flow
            authorized = mcp_service.oauth_service.has_usable_stored_authorization(
                server, tokens.get(server.serverName, {})
            )
            result[str(server.id)]["connection_state"] = status_resolver.resolve_oauth_state(flow, authorized)
        except Exception:
            logger.exception("Failed to resolve an OAuth server state")
            result[str(server.id)]["connection_state"] = ConnectionState.ERROR.value
    return result


async def get_single_server_connection_status(
    user_id: str,
    server_id: str,
    mcp_service: MCPService,
    server_service: ServerServiceV1,
    status_resolver: ConnectionStatusResolver,
) -> ServerConnectionStatus:
    server = await server_service.get_server_by_id(server_id)
    if server is None:
        raise ValueError(f"Server '{server_id}' not found")
    result = await get_servers_connection_status(user_id, [server], mcp_service, status_resolver)
    return result[str(server.id)]
