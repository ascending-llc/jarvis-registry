"""
Pydantic Schemas for Server Management API v1

These schemas define the request and response models for the
Server Management endpoints based on the API documentation.

All schemas use camelCase for API input/output and for MongoDB storage.
"""

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_serializer

from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.case_conversion import APIBaseModel
from registry.utils.crypto_utils import decrypt_auth_fields
from registry.utils.schema_converter import convert_dict_keys_to_camel

# ==================== Request Schemas ====================


def _validate_headers_value(headers: dict[str, Any] | None) -> dict[str, Any] | None:
    if headers is not None and isinstance(headers, dict):
        for key, value in headers.items():
            if not isinstance(value, (str, list)):
                raise ValueError(f"Header '{key}' must be a string or list of strings")
            if isinstance(value, list) and not all(isinstance(item, str) for item in value):
                raise ValueError(f"Header '{key}' list must contain only strings")
    return headers


class ServerCreateRequest(APIBaseModel):
    """Request schema for creating a new server"""

    # API to Database field name mapping
    _field_mapping = {
        "requiresOauth": "requiresOAuth",
    }

    title: str = Field(..., description="Display title of the MCP server")
    path: str = Field(..., description="Unique path/route for the server")
    description: str | None = Field(default="", description="Server description")
    url: str | None = Field(default=None, description="Backend proxy URL")
    tags: list[str] = Field(default_factory=list, description="Server tags")
    numTools: int = Field(default=0, description="Number of tools")
    numStars: int = Field(default=0, description="Star count")
    isPython: bool = Field(default=False, description="Is Python-based")
    license: str | None = Field(default=None, description="License type")
    authType: str | None = Field(default=None, description="Authentication type")
    authProvider: str | None = Field(default=None, description="Authentication provider")
    supportedTransports: list[str] = Field(default_factory=list, description="Supported transports")
    transport: str | dict[str, Any] | None = Field(default=None, description="Transport configuration (string or dict)")
    startup: bool = Field(default=False, description="Start on system startup")
    chatMenu: bool = Field(default=True, description="Show in chat menu")
    toolList: list[dict[str, Any]] = Field(default_factory=list, description="List of tools")
    iconPath: str | None = Field(default=None, description="Icon path")
    timeout: int | None = Field(default=30000, description="Request timeout (ms)")
    initTimeout: int | None = Field(default=60000, description="Init timeout (ms)")
    serverInstructions: str | None = Field(default=None, description="Usage instructions")
    requiresOauth: bool = Field(default=False, description="Requires OAuth")
    oauth: dict[str, Any] | None = Field(default=None, description="OAuth configuration")
    customUserVars: dict[str, Any] | None = Field(default=None, description="Custom variables")
    apiKey: dict[str, Any] | None = Field(default=None, description="API Key authentication configuration")
    headers: dict[str, Any] | None = Field(default=None, description="Custom headers (key/value pairs)")
    enabled: bool | None = Field(
        default=None, description="Whether the server is enabled (auto-set to False during registration)"
    )

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v):
        """Convert tags to lowercase for case-insensitive comparison"""
        if isinstance(v, list):
            return [tag.lower() if isinstance(tag, str) else tag for tag in v]
        return v

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, v):
        return _validate_headers_value(v)


class ServerUpdateRequest(APIBaseModel):
    """Request schema for updating a server (partial update)"""

    # API to Database field name mapping
    _field_mapping = {
        "requiresOauth": "requiresOAuth",
    }

    title: str | None = None
    path: str | None = None
    description: str | None = None
    url: str | None = None
    type: str | None = None
    tags: list[str] | None = None
    numTools: int | None = None
    numStars: int | None = None
    isPython: bool | None = None
    license: str | None = None
    authType: str | None = None
    authProvider: str | None = None
    supportedTransports: list[str] | None = None
    transport: str | dict[str, Any] | None = None
    startup: bool | None = None
    chatMenu: bool | None = None
    toolList: list[dict[str, Any]] | None = None
    iconPath: str | None = None
    timeout: int | None = None
    initTimeout: int | None = None
    serverInstructions: str | None = None
    requiresOauth: bool | None = None
    oauth: dict[str, Any] | None = None
    customUserVars: dict[str, Any] | None = None
    apiKey: dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    status: str | None = None
    enabled: bool | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, v):
        """Convert tags to lowercase"""
        if isinstance(v, list):
            return [tag.lower() if isinstance(tag, str) else tag for tag in v]
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        """Validate status values"""
        if v is not None:
            valid_statuses = ["active", "inactive", "error"]
            if v not in valid_statuses:
                raise ValueError(f"status must be one of {valid_statuses}")
        return v

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, v):
        return _validate_headers_value(v)


class ServerToggleRequest(APIBaseModel):
    """Request schema for toggling server status"""

    enabled: bool = Field(..., description="Enable or disable the server")


class ServerConnectionTestRequest(APIBaseModel):
    """Request schema for testing connection to an MCP server URL"""

    url: str = Field(..., description="MCP server URL to test connection")
    transport: str | None = Field(default="streamable-http", description="Transport type (streamable-http, sse, stdio)")


# ==================== Response Schemas ====================


class ToolSchema(APIBaseModel):
    """Schema for a tool definition"""

    name: str
    description: str
    inputSchema: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class ServerListItemResponse(APIBaseModel):
    """Response schema for a server in the list"""

    id: str = Field(..., description="Server ID")
    serverName: str = Field(..., description="Server name")
    title: str | None = Field(None, description="Display title for the server")
    description: str | None = None
    type: str | None = Field(None, description="Transport type (e.g., streamable-http, sse, stdio)")
    url: str | None = None
    apiKey: dict[str, Any] | None = None
    oauth: dict[str, Any] | None = None
    headers: dict[str, Any] | None = Field(None, description="Custom headers (key/value pairs)")
    requiresOauth: bool = Field(False, description="Whether OAuth is required")
    capabilities: str | None = Field(None, description="JSON string of server capabilities")
    oauthMetadata: dict[str, Any] | None = Field(None, description="OAuth metadata from autodiscovery")
    tools: str | None = Field(None, description="Comma-separated list of tool names")
    author: str | None = Field(None, description="Author user ID")
    status: str = "active"
    path: str | None = Field(None, description="API path for this server")
    tags: list[str] = Field(default_factory=list)
    numTools: int = Field(0, description="Number of tools")
    numStars: int = Field(0, description="Star count")
    enabled: bool = Field(default=True, description="Whether the server is enabled")
    lastConnected: datetime | None = Field(None, description="Last connection timestamp")
    createdAt: datetime
    updatedAt: datetime
    connectionState: str | None = Field(default=None, description="Connection state")
    error: str | None = Field(default=None, description="Error message if connection failed")
    permissions: ResourcePermissions | None = Field(
        default=None, description="Resolved ACL permissions for the current user"
    )

    model_config = ConfigDict(from_attributes=True)

    @model_serializer(mode="wrap")
    def _serialize(self, serializer, info):
        data = serializer(self)
        has_oauth = data.get("oauth") is not None
        has_api_key = data.get("apiKey") is not None

        if has_oauth:
            data.pop("apiKey", None)
        elif has_api_key:
            data.pop("oauth", None)
        else:
            data.pop("oauth", None)
            data.pop("apiKey", None)
        return data


class ServerDetailResponse(APIBaseModel):
    """
    Unified response schema for server detail operations

    Used for:
    - GET /servers/{path} - Get server details
    - POST /servers - Create server
    - PUT /servers/{path} - Update server
    - POST /servers/{path}/toggle - Toggle server
    - GET /servers/{path}/tools - Get server tools
    - POST /servers/{path}/health - Health check
    """

    id: str
    serverName: str = Field(..., description="Server name")
    title: str | None = Field(None, description="Display title for the server")
    description: str | None = None
    type: str | None = Field(None, description="Transport type (e.g., streamable-http, sse, stdio)")
    url: str | None = None
    apiKey: dict[str, Any] | None = None
    oauth: dict[str, Any] | None = None
    headers: dict[str, Any] | None = Field(None, description="Custom headers (key/value pairs)")
    requiresOauth: bool = Field(False, description="Whether OAuth is required")
    capabilities: str | None = Field(None, description="JSON string of server capabilities")
    oauthMetadata: dict[str, Any] | None = Field(None, description="OAuth metadata from autodiscovery")
    tools: str | None = Field(None, description="Comma-separated list of tool names")
    toolFunctions: dict[str, Any] | None = Field(None, description="Complete OpenAI function schemas with mcpToolName")
    resources: list[dict[str, Any]] | None = Field(None, description="List of available resources")
    prompts: list[dict[str, Any]] | None = Field(None, description="List of available prompts")
    initDuration: int | None = Field(None, description="Initialization duration in ms")
    author: str | None = Field(None, description="Author user ID")
    status: str
    path: str | None = Field(None, description="API path for this server")
    tags: list[str] = Field(default_factory=list)
    numTools: int = Field(0, description="Number of tools")
    numStars: int = Field(0, description="Star count")
    enabled: bool = Field(default=True, description="Whether the server is enabled")
    lastConnected: datetime | None = Field(None, description="Last connection timestamp")
    lastError: str | None = Field(None, description="Last error timestamp")
    errorMessage: str | None = Field(None, description="Error message")
    createdAt: datetime
    updatedAt: datetime
    connectionState: str | None = Field(default=None, description="Connection state")
    error: str | None = Field(default=None, description="Error message if connection failed")
    permissions: ResourcePermissions | None = Field(
        default=None, description="Resolved ACL permissions for the current user"
    )

    model_config = ConfigDict(from_attributes=True)

    @model_serializer(mode="wrap")
    def _serialize(self, serializer, info):
        data = serializer(self)
        has_oauth = data.get("oauth") is not None
        has_api_key = data.get("apiKey") is not None

        if has_oauth:
            data.pop("apiKey", None)
        elif has_api_key:
            data.pop("oauth", None)
        else:
            data.pop("oauth", None)
            data.pop("apiKey", None)
        return data


class ServerConnectionTestResponse(APIBaseModel):
    """Response schema for connection test"""

    success: bool = Field(..., description="Whether the connection test was successful")
    message: str = Field(..., description="Descriptive message about the connection result")
    serverName: str | None = Field(None, description="MCP server name from serverInfo")
    protocolVersion: str | None = Field(None, description="MCP protocol version")
    responseTimeMs: int | None = Field(None, description="Response time in milliseconds")
    capabilities: dict[str, Any] | None = Field(None, description="Server capabilities")
    error: str | None = Field(None, description="Error message if connection failed")

    model_config = ConfigDict(from_attributes=True)


class PaginationMetadata(APIBaseModel):
    """Pagination metadata"""

    total: int
    page: int
    perPage: int
    totalPages: int


class ServerListResponse(APIBaseModel):
    """Response schema for server list with pagination"""

    servers: list[ServerListItemResponse]
    pagination: PaginationMetadata


class ErrorResponse(APIBaseModel):
    """Error response schema"""

    error: str
    message: str


class ServerStatsResponse(APIBaseModel):
    """Response schema for server statistics (Admin only)"""

    totalServers: int = Field(..., description="Total number of servers")
    serversByStatus: dict[str, int] = Field(..., description="Server count grouped by status")
    serversByTransport: dict[str, int] = Field(..., description="Server count grouped by transport type")
    totalTokens: int = Field(..., description="Total number of tokens")
    tokensByType: dict[str, int] = Field(..., description="Token count grouped by type")
    activeTokens: int = Field(..., description="Number of active (non-expired) tokens")
    expiredTokens: int = Field(..., description="Number of expired tokens")
    activeUsers: int = Field(..., description="Number of users with active tokens")
    totalTools: int = Field(..., description="Total number of tools across all servers")

    model_config = ConfigDict(from_attributes=True)


# ==================== Helper Functions ====================


def _get_config_field(server, field: str, default=None):
    """Extract a field from server.config with fallback to default"""
    if not server or not hasattr(server, "config") or not server.config:
        return default
    return server.config.get(field, default)


def _mask_oauth_client_secret(oauth_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Mask OAuth client_secret to show only the last 6 characters.
    """
    if not oauth_config or not isinstance(oauth_config, dict):
        return oauth_config

    masked_oauth = oauth_config.copy()

    if "client_secret" in masked_oauth and masked_oauth["client_secret"]:
        client_secret = str(masked_oauth["client_secret"])
        if len(client_secret) > 6:
            masked_oauth["client_secret"] = "************" + client_secret[-6:]

    return masked_oauth


def _mask_apikey(apikey_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Mask API Key to show only the last 6 characters.
    """
    if not apikey_config or not isinstance(apikey_config, dict):
        return apikey_config

    masked_apikey = apikey_config.copy()

    if "key" in masked_apikey and masked_apikey["key"]:
        key_value = str(masked_apikey["key"])
        if len(key_value) > 6:
            masked_apikey["key"] = "************" + key_value[-6:]

    return masked_apikey


def convert_to_list_item(
    server,
    acl_permission: ResourcePermissions | None = None,
) -> ServerListItemResponse:
    """Convert ExtendedMCPServer to ServerListItemResponse"""
    config = server.config or {}
    config = decrypt_auth_fields(config)

    # Mask first (reads snake_case keys), then convert to camelCase for API response
    oauth_config = convert_dict_keys_to_camel(_mask_oauth_client_secret(config.get("oauth")))
    apikey_config = _mask_apikey(config.get("apiKey"))

    author_id = str(server.author) if server.author else None
    transport_type = config.get("type", "streamable-http")
    title = config.get("title") or server.serverName
    tools_str = config.get("tools", "")
    capabilities_str = config.get("capabilities", "{}")
    num_tools = server.numTools if hasattr(server, "numTools") else 0

    return ServerListItemResponse(
        id=str(server.id),
        serverName=server.serverName,
        title=title,
        description=config.get("description"),
        type=transport_type,
        url=config.get("url"),
        apiKey=apikey_config,
        oauth=oauth_config,
        headers=config.get("headers"),
        requiresOauth=config.get("requiresOAuth", False),
        capabilities=capabilities_str,
        oauthMetadata=convert_dict_keys_to_camel(config.get("oauthMetadata")),
        tools=tools_str,
        author=author_id,
        status=server.status,
        path=server.path,
        tags=server.tags,
        numTools=num_tools,
        numStars=server.numStars,
        enabled=config.get("enabled", True),
        lastConnected=server.lastConnected,
        createdAt=server.createdAt or datetime.now(),
        updatedAt=server.updatedAt or datetime.now(),
        permissions=acl_permission,
    )


def convert_to_detail(
    server,
    acl_permission: ResourcePermissions | None = None,
) -> ServerDetailResponse:
    """Convert ExtendedMCPServer to ServerDetailResponse"""
    config = server.config or {}
    config = decrypt_auth_fields(config)

    # Mask first (reads snake_case keys), then convert to camelCase for API response
    oauth_config = convert_dict_keys_to_camel(_mask_oauth_client_secret(config.get("oauth")))
    apikey_config = _mask_apikey(config.get("apiKey"))

    author_id = str(server.author) if server.author else None
    transport_type = config.get("type", "streamable-http")
    title = config.get("title") or server.serverName
    tools_str = config.get("tools", "")
    tool_functions = config.get("toolFunctions")
    resources = config.get("resources", [])
    prompts = config.get("prompts", [])
    capabilities_str = config.get("capabilities", "{}")
    num_tools = server.numTools if hasattr(server, "numTools") else 0

    last_error_str = None
    if server.lastError:
        last_error_str = (
            server.lastError.isoformat() if isinstance(server.lastError, datetime) else str(server.lastError)
        )

    return ServerDetailResponse(
        id=str(server.id),
        serverName=server.serverName,
        title=title,
        description=config.get("description"),
        type=transport_type,
        url=config.get("url"),
        apiKey=apikey_config,
        oauth=oauth_config,
        headers=config.get("headers"),
        requiresOauth=config.get("requiresOAuth", False),
        capabilities=capabilities_str,
        oauthMetadata=convert_dict_keys_to_camel(config.get("oauthMetadata")),
        tools=tools_str,
        toolFunctions=tool_functions,
        resources=resources,
        prompts=prompts,
        initDuration=config.get("initDuration"),
        author=author_id,
        status=server.status,
        path=server.path,
        tags=server.tags,
        numTools=num_tools,
        numStars=server.numStars,
        enabled=config.get("enabled", True),
        lastConnected=server.lastConnected,
        lastError=last_error_str,
        errorMessage=server.errorMessage if hasattr(server, "errorMessage") else None,
        createdAt=server.createdAt or datetime.now(),
        updatedAt=server.updatedAt or datetime.now(),
        permissions=acl_permission,
    )
