import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..core.config import settings
from ..services.server_service import server_service_v1 as server_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_required_env_vars() -> tuple[str, str, str]:
    """
    Get required settings for protected resource endpoints.

    Returns:
        Tuple of (base_url, auth_server_url, registry_url) where:
            - base_url: Base origin without prefix (for RFC 8705 issuer compliance)
            - auth_server_url: Full URL with prefix (for OAuth operational endpoints)
            - registry_url: Registry client URL

    Raises:
        HTTPException: If required settings are not configured
    """
    auth_server_url = settings.auth_server_external_url
    registry_url = settings.registry_client_url

    if not auth_server_url:
        logger.error("auth_server_external_url setting is not configured")
        raise HTTPException(
            status_code=500, detail="Server configuration error: auth_server_external_url not configured"
        )

    if not registry_url:
        logger.error("registry_client_url setting is not configured")
        raise HTTPException(status_code=500, detail="Server configuration error: registry_client_url not configured")

    # Strip the API prefix from auth server URL to get base URL
    # This is required for RFC 8705 compliance - the authorization server URL
    # in Protected Resource Metadata must match the issuer in the auth server's metadata
    base_url = auth_server_url
    if settings.auth_server_api_prefix:
        prefix = settings.auth_server_api_prefix.rstrip("/")
        if auth_server_url.endswith(prefix):
            base_url = auth_server_url[: -len(prefix)].rstrip("/")

    return base_url, auth_server_url, registry_url


# @router.get("/mcp-servers")
async def get_wellknown_mcp_servers(request: Request, user_context: dict | None = None) -> JSONResponse:
    """
    Main endpoint handler for /.well-known/mcp-servers
    Returns JSON with all discoverable MCP servers
    """
    # Step 1: Check if discovery is enabled
    if not settings.enable_wellknown_discovery:
        raise HTTPException(status_code=404, detail="Well-known discovery is disabled")

    # Step 2: Get all servers from server_service
    all_servers = server_service.get_all_servers()

    # Step 3: Filter based on discoverability and permissions
    discoverable_servers = []
    for server_path, server_info in all_servers.items():
        # For now, include all enabled servers
        # TODO: Add discoverability flag to server configs if needed
        if server_service.is_service_enabled(server_path):
            formatted_server = _format_server_discovery(server_info, request)
            discoverable_servers.append(formatted_server)

    # Step 4: Format response
    response_data = {
        "version": "1.0",
        "servers": discoverable_servers,
        "registry": {
            "name": "Enterprise MCP Gateway",
            "description": "Centralized MCP server registry for enterprise tools",
            "version": "1.0.0",
            "contact": {"url": str(request.base_url).rstrip("/"), "support": "mcp-support@company.com"},
        },
    }

    # Step 5: Return JSONResponse with cache headers
    headers = {"Cache-Control": f"public, max-age={settings.wellknown_cache_ttl}", "Content-Type": "application/json"}

    logger.info(f"Returned {len(discoverable_servers)} servers for well-known discovery")
    return JSONResponse(content=response_data, headers=headers)


def _format_server_discovery(server_info: dict, request: Request) -> dict:
    """Format individual server for discovery response"""
    server_path = server_info.get("path", "")
    server_name = server_info.get("server_name", server_path)
    description = server_info.get("description", "MCP Server")

    # Generate dynamic URL based on request host
    server_url = _get_server_url(server_path, request)

    # Get transport type from config
    transport_type = _get_transport_type(server_info)

    # Get authentication requirements
    auth_info = _get_authentication_info(server_info)

    # Get first 5 tools as preview
    tools_preview = _get_tools_preview(server_info, max_tools=5)

    return {
        "name": server_name,
        "description": description,
        "url": server_url,
        "transport": transport_type,
        "authentication": auth_info,
        "capabilities": ["tools", "resources"],
        "health_status": "healthy",  # TODO: Get actual health status
        "tools_preview": tools_preview,
    }


def _get_server_url(server_path: str, request: Request) -> str:
    """Generate full URL for MCP server based on request host"""
    # Get host from request headers
    registry_url = settings.registry_client_url.rstrip("/")

    # Clean up server path (remove leading and trailing slashes)
    clean_path = server_path.strip("/")

    # Return formatted URL
    return f"{registry_url}/{clean_path}/mcp"


def _get_transport_type(server_config: dict) -> str:
    """Determine transport type (sse or streamable-http)"""
    # Check server configuration for transport setting
    # Default to "streamable-http" if not specified
    return server_config.get("transport", "streamable-http")


def _get_authentication_info(server_info: dict) -> dict:
    """Extract authentication requirements for server"""
    auth_type = server_info.get("auth_type", "oauth")
    auth_provider = server_info.get("auth_provider", "default")
    server_name = server_info.get("server_name", "unknown")

    # Map auth types to standard formats
    if auth_type == "oauth":
        return {
            "type": "oauth2",
            "required": True,
            "authorization_url": "/auth/oauth/authorize",
            "provider": auth_provider,
            "scopes": ["mcp:read", f"{auth_provider}:read"],
        }
    elif auth_type == "api-key":
        return {"type": "api-key", "required": True, "header": "X-API-Key"}
    else:
        # Default to OAuth2 for unknown types
        return {
            "type": "oauth2",
            "required": True,
            "authorization_url": "/auth/oauth/authorize",
            "scopes": ["mcp:read", f"{server_name.lower()}:read"],
        }


def _get_tools_preview(server_info: dict, max_tools: int = 5) -> list:
    """Get limited list of tools for discovery preview"""
    # Extract tools from server_info
    tools = server_info.get("tool_list", [])

    # Return first N tools with name and description
    preview_tools = []
    for tool in tools[:max_tools]:
        if isinstance(tool, dict):
            # Try to get description from parsed_description.main first, then fall back to description field
            description = tool.get("parsed_description", {}).get(
                "main", tool.get("description", "No description available")
            )
            preview_tools.append({"name": tool.get("name", "unknown"), "description": description})
        elif isinstance(tool, str):
            # Handle case where tools are just strings
            preview_tools.append({"name": tool, "description": "No description available"})

    return preview_tools


@router.get("/oauth-protected-resource")
async def oauth_protected_resource_metadata() -> JSONResponse:
    """
    OAuth 2.0 Protected Resource Metadata (RFC 8705).

    Describes the protected resource (MCP Registry/Gateway) and its authentication
    requirements. This endpoint helps MCP clients discover the authentication
    requirements for accessing the registry's proxied MCP servers.
    """
    base_url, auth_server_url, registry_url = _get_required_env_vars()

    data = {
        "resource": registry_url,
        "authorization_servers": [base_url],
        "scopes_supported": [
            "servers-read",
            "agents-read",
            "agents-write",
            "server-write",
            "servers-share",
            "agents-share",
        ],
        "bearer_methods_supported": ["header"],
        "resource_signing_alg_values_supported": ["HS256", "RS256"],
    }

    return JSONResponse(content=data)


@router.get("/oauth-protected-resource/mcp")
async def mcp_oauth_metadata() -> JSONResponse:
    """
    MCP-specific OAuth metadata endpoint.

    Provides MCP-specific authentication and authorization metadata for
    MCP clients like Claude Desktop. This endpoint follows the MCP OAuth
    specification for protected resource discovery.
    """
    base_url, auth_server_url, registry_url = _get_required_env_vars()

    data = {
        "resource": f"{registry_url}/proxy",
        "authorization_server": base_url,
        "device_authorization_endpoint": f"{auth_server_url}/oauth2/device/code",
        "token_endpoint": f"{auth_server_url}/oauth2/token",
        "verification_uri": f"{auth_server_url}/oauth2/device/verify",
        "scopes_supported": ["servers-read", "agents-read", "user-read"],
        "grant_types_supported": ["urn:ietf:params:oauth:grant-type:device_code"],
        "mcp_version": "2024-11-05",
    }

    return JSONResponse(content=data)


@router.get("/oauth-protected-resource/{server_path:path}")
async def oauth_protected_resource_metadata_for_server(server_path: str, request: Request) -> JSONResponse:
    """
    OAuth 2.0 Protected Resource Metadata (RFC 8705) for a specific proxied server.

    Allows MCP clients to discover OAuth metadata for individual proxied MCP servers.
    For example: /.well-known/oauth-protected-resource/proxy/mcpgw
    """
    base_url, auth_server_url, registry_url = _get_required_env_vars()

    # Clean server path (remove leading/trailing slashes)
    clean_path = server_path.strip("/")
    resource_url = f"{registry_url}/{clean_path}"

    data = {
        "resource": resource_url,
        "authorization_servers": [base_url],
        "scopes_supported": ["servers-read", "agents-read", "user-read"],
        "bearer_methods_supported": ["header"],
        "resource_signing_alg_values_supported": ["HS256", "RS256"],
    }

    return JSONResponse(content=data)


@router.get("/oauth-protected-resource/mcp/{server_path:path}")
async def mcp_oauth_metadata_for_server(server_path: str, request: Request) -> JSONResponse:
    """
    MCP-specific OAuth metadata endpoint for a specific proxied server.

    Provides MCP-specific authentication and authorization metadata for
    individual proxied servers. For example: /.well-known/oauth-protected-resource/mcp/proxy/mcpgw
    """
    base_url, auth_server_url, registry_url = _get_required_env_vars()

    # Clean server path (remove leading/trailing slashes)
    clean_path = server_path.strip("/")

    resource_url = f"{registry_url}/{clean_path}"

    data = {
        "resource": resource_url,
        "authorization_server": base_url,
        "device_authorization_endpoint": f"{auth_server_url}/oauth2/device/code",
        "token_endpoint": f"{auth_server_url}/oauth2/token",
        "verification_uri": f"{auth_server_url}/oauth2/device/verify",
        "scopes_supported": ["servers-read", "agents-read", "user-read"],
        "grant_types_supported": ["urn:ietf:params:oauth:grant-type:device_code"],
        "mcp_version": "2024-11-05",
    }

    return JSONResponse(content=data)
