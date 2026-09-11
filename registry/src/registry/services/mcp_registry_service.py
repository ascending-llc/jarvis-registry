import logging

from pydantic import ValidationError

from registry_pkgs.models import ExtendedMCPServer

from ..core.config import Settings
from ..schemas.mcp_registry_schema import (
    DESCRIPTION_MAX_LENGTH,
    Input,
    PaginationMetadata,
    ServerJSON,
    ServerListResponse,
    ServerResponse,
    Transport,
)

logger = logging.getLogger(__name__)

_DISCOVER_EXECUTE_SLUG = "discover-execute"

_ELIGIBILITY_FILTER = {
    "config.enabled": True,
    "config.type": "streamable-http",
    "path": {"$type": "string"},
}


def _namespaced_name(settings: Settings, slug: str) -> str:
    return f"{settings.mcp_registry_namespace}/{slug}"


def _effective_version(settings: Settings) -> str:
    """build_version, falling back to 0.0.0 when unset."""
    return settings.build_version or "0.0.0"


def _server_description(server: ExtendedMCPServer) -> str:
    """Description for a catalog entry, truncated to the spec's 200-char limit."""
    raw = (server.config.get("description") or "").strip()
    if len(raw) > DESCRIPTION_MAX_LENGTH:
        logger.warning(
            "Truncating description for server '%s' from %d to %d chars",
            server.serverName,
            len(raw),
            DESCRIPTION_MAX_LENGTH,
        )
        raw = raw[:DESCRIPTION_MAX_LENGTH]
    return raw or server.serverName


def build_discover_execute_entry(settings: Settings) -> ServerResponse:
    """The single fixed entry pointing at the MCP gateway; not per-user, so no variables."""
    url = f"{settings.registry_url}/proxy/mcpgw/mcp"
    server = ServerJSON(
        name=_namespaced_name(settings, _DISCOVER_EXECUTE_SLUG),
        description="Discover and execute tools, resources, and prompts across the Jarvis Registry.",
        version=_effective_version(settings),
        title="Jarvis Registry Gateway",
        remotes=[Transport(type="streamable-http", url=url)],
    )
    return ServerResponse(server=server)


def build_server_entry(server: ExtendedMCPServer, settings: Settings) -> ServerResponse:
    """One catalog entry for a downstream server, pointing at the direct-connect proxy."""
    namespace = settings.mcp_registry_namespace
    url = f"{settings.registry_url}/proxy/server/{{userId}}{server.path}"
    server_json = ServerJSON(
        name=_namespaced_name(settings, server.serverName),
        title=server.serverName,
        description=_server_description(server),
        version=_effective_version(settings),
        remotes=[
            Transport(
                type="streamable-http",
                url=url,
                variables={"userId": Input(description="Your Jarvis Registry user ID", isRequired=True)},
            )
        ],
        meta={f"{namespace}/internal": {"path": server.path, "tags": server.tags}},
    )
    return ServerResponse(server=server_json)


def _clamp_limit(limit: int | None, settings: Settings) -> int:
    if limit is None:
        return settings.mcp_registry_default_limit
    return max(1, min(limit, settings.mcp_registry_max_limit))


async def list_registry_entries(
    settings: Settings,
    cursor: str | None = None,
    limit: int | None = None,
) -> ServerListResponse:
    """List the catalog: the fixed gateway entry plus every eligible downstream server.

    Sorted by ``name`` so the cursor (the last-returned entry's ``name``, an opaque string per
    spec) yields a stable resume point.
    """
    effective_limit = _clamp_limit(limit, settings)

    servers = await ExtendedMCPServer.find(_ELIGIBILITY_FILTER).to_list()
    entries = [build_discover_execute_entry(settings)]
    for server in servers:
        try:
            entries.append(build_server_entry(server, settings))
        except ValidationError:
            logger.warning("Excluding server %r from registry catalog: fails server.json schema", server.serverName)
    entries.sort(key=lambda entry: entry.server.name)

    start = 0
    if cursor is not None:
        # Resume after the entry whose name equals the cursor.
        start = next((i + 1 for i, e in enumerate(entries) if e.server.name == cursor), len(entries))

    page = entries[start : start + effective_limit]
    next_cursor = page[-1].server.name if len(entries) > start + effective_limit and page else None

    return ServerListResponse(
        servers=page,
        metadata=PaginationMetadata(count=len(page), nextCursor=next_cursor),
    )


def _version_matches(version: str, settings: Settings) -> bool:
    return version == "latest" or version == _effective_version(settings)


async def get_registry_entry(server_name: str, version: str, settings: Settings) -> ServerResponse | None:
    """Look up one catalog entry by its full namespaced name and version.

    Returns ``None`` (the route turns it into a 404) when the name/version does not resolve.
    """
    if not _version_matches(version, settings):
        return None

    if server_name == _namespaced_name(settings, _DISCOVER_EXECUTE_SLUG):
        return build_discover_execute_entry(settings)

    prefix = f"{settings.mcp_registry_namespace}/"
    if not server_name.startswith(prefix):
        return None
    stored_name = server_name[len(prefix) :]

    server = await ExtendedMCPServer.find_one({"serverName": stored_name, **_ELIGIBILITY_FILTER})
    if server is None:
        return None
    try:
        return build_server_entry(server, settings)
    except ValidationError:
        # A malformed serverName should 404 (absent from the catalog), never 500.
        logger.warning("Server %r fails server.json schema; treating as not found", stored_name)
        return None
