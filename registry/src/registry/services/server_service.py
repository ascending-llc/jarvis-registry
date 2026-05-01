"""
Server Service V1 - Business logic for Server Management API

This service handles all server-related operations using MongoDB and Beanie ODM.

ODM Schema:
- serverName: str (required)
- config: Dict[str, Any] (required) - stores all server configuration
- author: PydanticObjectId (required) - references user who created the server
- createdAt: datetime (auto-generated)
- updatedAt: datetime (auto-generated)
"""

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from redis import Redis

from registry_pkgs.database.decorators import get_current_session
from registry_pkgs.models import (
    ExtendedMCPServer,
    Token,
)
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.models.federation import AgentCoreRuntimeAccessConfig
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from ..auth.oauth.types import StateMetadata
from ..core.config import settings
from ..core.mcp_client import get_oauth_metadata_from_server
from ..core.telemetry_decorators import track_tool_discovery
from ..schemas.errors import (
    AuthenticationError,
    MissingUserIdError,
    OAuthReAuthRequiredError,
    OAuthTokenError,
)
from ..schemas.server_api_schemas import (
    ServerCreateRequest,
    ServerUpdateRequest,
)
from ..utils.crypto_utils import decrypt_auth_fields, encrypt_auth_fields, generate_service_jwt
from ..utils.schema_converter import convert_dict_keys_to_snake
from ..utils.utils import generate_server_name_from_title, normalize_headers
from .oauth.oauth_service import MCPOAuthService
from .oauth.token_service import TokenService
from .user_service import UserService

logger = logging.getLogger(__name__)


def _extract_config_field(server: ExtendedMCPServer, field: str, default: Any = None) -> Any:
    """Extract a field from server.config with fallback to default"""
    if not server or not server.config:
        return default
    return server.config.get(field, default)


def _build_server_info_for_mcp_client(config: dict[str, Any], tags: list[str]) -> dict[str, Any]:
    """
    Build server_info dictionary for MCP client operations.

    Args:
        config: Server config dictionary
        tags: Server tags list

    Returns:
        server_info dictionary with type, tags, headers, and apiKey (if present)
    """
    server_info = {
        "type": config.get("type", "streamable-http"),
        "tags": tags or [],
    }

    # Add optional fields if present
    if "headers" in config:
        server_info["headers"] = config["headers"]

    if "apiKey" in config:
        server_info["apiKey"] = config["apiKey"]

    return server_info


async def build_complete_headers_for_server(
    oauth_service: MCPOAuthService,
    server: ExtendedMCPServer,
    user_id: str | None = None,
    *,
    state_metadata: StateMetadata | None = None,
    redis_client: Redis | None = None,
) -> dict[str, str]:
    """
    Build complete HTTP headers with ALL authentication types.
    Consolidates OAuth, apiKey, custom header, and AgentCore Runtime auth logic in one place.

    This eliminates duplicate header building across server_service, proxy_routes, and health_service.

    Args:
        oauth_service: OAuth service for OAuth token management
        server: Server document containing config
        user_id: User ID for OAuth token retrieval (required for OAuth servers)
        state_metadata: OAuth flow state metadata
        redis_client: Redis client for JWT token caching

    Returns:
        Complete headers dictionary ready for HTTP requests

    Raises:
        MissingUserIdError: If OAuth server requires user_id but none provided
        OAuthReAuthRequiredError: If OAuth re-authentication is needed
        OAuthTokenError: If OAuth token retrieval/refresh fails
        AuthenticationError: For other authentication failures
    """

    config = server.config or {}
    decrypted_config = decrypt_auth_fields(config)

    # Start with base MCP headers
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": settings.registry_app_name,
    }

    # Always add internal service JWT if user_id is provided
    if user_id:
        service_jwt = generate_service_jwt(user_id)
        headers[settings.internal_auth_header] = f"Bearer {service_jwt}"
        logger.debug(f"Added internal service JWT to {settings.internal_auth_header} header for user {user_id}")

    # 1. Add custom headers FIRST (lowest priority)
    custom_headers = normalize_headers(decrypted_config.get("headers"))
    logger.info(f"custom headers: {custom_headers}")
    if custom_headers:
        headers.update(custom_headers)

    # 2. Check AgentCore Runtime authentication (for federated AgentCore MCP servers)
    runtime_access_config = decrypted_config.get("runtimeAccess")

    if runtime_access_config:
        # AgentCore Runtime server with runtimeAccess configuration
        try:
            # Parse runtime access config
            if isinstance(runtime_access_config, dict):
                access_config = AgentCoreRuntimeAccessConfig(**runtime_access_config)
            else:
                access_config = runtime_access_config

            # Only handle JWT mode for now (IAM support can be added later if needed)
            if access_config.mode == AgentCoreRuntimeAccessMode.JWT:
                logger.info(f"Building JWT token for AgentCore Runtime server {server.serverName}")

                # Generate cache key for JWT token
                cache_key = f"{settings.redis_key_prefix}:agentcore_jwt:{server.id}"

                # Try to get cached JWT token
                cached_token = None
                if redis_client:
                    try:
                        cached_value = redis_client.get(cache_key)
                        if cached_value:
                            cached_token = (
                                cached_value.decode("utf-8") if isinstance(cached_value, bytes) else cached_value
                            )
                            logger.debug(f"Using cached JWT token for {server.serverName}")
                    except Exception:
                        logger.exception(f"Failed to get cached JWT for {server.serverName}")

                # Use cached token if available
                if cached_token:
                    headers["Authorization"] = f"Bearer {cached_token}"
                    logger.info(f"Added cached AgentCore Runtime JWT for {server.serverName}")
                    return headers

                # Generate new JWT token (AWS only validates iss, aud, and signature)
                token = generate_service_jwt(
                    for_agentcore_runtime=True,
                    expires_in_seconds=300,
                )

                # Cache the JWT token (270 seconds = 300s token TTL - 30s buffer)
                if redis_client:
                    try:
                        redis_client.setex(cache_key, 270, token)
                        logger.debug(f"Cached JWT token for {server.serverName} (TTL: 270s)")
                    except Exception:
                        logger.exception(f"Failed to cache JWT for {server.serverName}")

                # Set Authorization header
                headers["Authorization"] = f"Bearer {token}"
                logger.info(f"Added AgentCore Runtime JWT for {server.serverName}")
                return headers

            elif access_config.mode == AgentCoreRuntimeAccessMode.IAM:
                raise NotImplementedError(
                    f"IAM authentication not yet supported for AgentCore Runtime server {server.serverName}"
                )
            else:
                logger.warning(f"Unknown runtime access mode '{access_config.mode}' for server {server.serverName}")
        except NotImplementedError:
            raise
        except Exception as exc:
            logger.exception(f"Failed to build AgentCore Runtime authentication for {server.serverName}")
            raise AuthenticationError(f"Failed to authenticate with AgentCore Runtime: {exc}")
    elif decrypted_config.get("authProvider") == "bedrock-agentcore":
        # Server has authProvider but no runtimeAccess configuration
        logger.warning(
            f"Server {server.serverName} has authProvider='bedrock-agentcore' "
            f"but missing runtimeAccess configuration. Skipping runtime authentication."
        )

    # 3. Check OAuth and add OAuth headers (high priority, overrides custom headers)
    requires_oauth = decrypted_config.get("requiresOAuth", False) or "oauth" in decrypted_config

    if requires_oauth:
        if not user_id:
            raise MissingUserIdError(
                f"User ID required for OAuth server {server.serverName}",
                server_name=server.serverName,
            )

        logger.info(f"Building OAuth headers for {server.serverName}")

        # Validate and merge OAuth metadata with config.oauth as source of truth
        # This ensures correct authorization_servers are used for token validation
        oauth_config = decrypted_config.get("oauth")
        raw_oauth_metadata = decrypted_config.get("oauthMetadata", {})

        oauth_metadata = _validate_and_merge_oauth_metadata(
            oauth_config=oauth_config, oauth_metadata=raw_oauth_metadata
        )

        # Update server's oauthMetadata in-memory for this request
        # This ensures OAuth service uses correct authorization_servers
        if oauth_metadata:
            config["oauthMetadata"] = oauth_metadata
            server.config = config
            logger.debug(
                f"Validated OAuth metadata for token retrieval: authorization_servers={oauth_metadata.get('authorization_servers')}"
            )

        # Get OAuth token (handles refresh automatically)
        access_token, auth_url, error = await oauth_service.get_valid_access_token(
            user_id=user_id, server=server, state_metadata=state_metadata
        )

        if auth_url:
            raise OAuthReAuthRequiredError(
                f"OAuth re-authentication required for {server.serverName}",
                auth_url=auth_url,
                server_name=server.serverName,
            )

        if error:
            raise OAuthTokenError(
                f"OAuth token error for {server.serverName}: {error}",
                server_name=server.serverName,
            )

        if not access_token:
            raise OAuthTokenError(
                f"No valid OAuth token available for {server.serverName}",
                server_name=server.serverName,
            )

        # Override any existing Authorization header with OAuth Bearer token
        # This ensures OAuth always takes priority over custom headers
        headers["Authorization"] = f"Bearer {access_token}"
        logger.debug(f"OAuth Bearer token added for {server.serverName} (overrides any custom Authorization header)")
        return headers

    # 4. Handle apiKey authentication (if not OAuth or AgentCore Runtime)
    api_key_config = decrypted_config.get("apiKey")
    if api_key_config and isinstance(api_key_config, dict):
        key_value = api_key_config.get("key")
        authorization_type = api_key_config.get("authorization_type", "bearer").lower()

        if key_value:
            if authorization_type == "bearer":
                headers["Authorization"] = f"Bearer {key_value}"
                logger.debug(f"Added Bearer apiKey for {server.serverName}")
            elif authorization_type == "basic":
                # Handle base64 encoding
                try:
                    base64.b64decode(key_value, validate=True)
                    # Already base64 encoded
                    headers["Authorization"] = f"Basic {key_value}"
                    logger.debug(f"Added Basic auth (pre-encoded) for {server.serverName}")
                except Exception:
                    # Not base64 encoded, encode it
                    encoded_key = base64.b64encode(key_value.encode()).decode()
                    headers["Authorization"] = f"Basic {encoded_key}"
                    logger.debug(f"Added Basic auth (auto-encoded) for {server.serverName}")
            elif authorization_type == "custom":
                custom_header = api_key_config.get("custom_header")
                if custom_header:
                    headers[custom_header] = key_value
                    logger.debug(f"Added custom auth header '{custom_header}' for {server.serverName}")
                else:
                    logger.warning(
                        f"apiKey with authorization_type='custom' but no custom_header for {server.serverName}"
                    )
            else:
                logger.warning(
                    f"Unknown authorization_type: {authorization_type}, defaulting to Bearer for {server.serverName}"
                )
                headers["Authorization"] = f"Bearer {key_value}"

    return headers


def _validate_and_merge_oauth_metadata(
    oauth_config: dict[str, Any] | None, oauth_metadata: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Merge OAuth metadata using database config.oauth as authoritative source.

    Database config.oauth (configured by admin) always takes priority over
    MCP server's .well-known metadata to prevent incorrect configurations.

    Args:
        oauth_config: OAuth configuration from registry database (config.oauth) - AUTHORITATIVE
        oauth_metadata: OAuth metadata from MCP server's /.well-known endpoint

    Returns:
        Merged OAuth metadata with database config.oauth overriding server metadata

    Example:
        Database config.oauth.authorization_servers: ["https://accounts.google.com"]
        Server metadata.authorization_servers: ["http://localhost:3080/"]  # WRONG
        Result: authorization_servers = ["https://accounts.google.com"] (from database config)
    """
    # If neither metadata nor config is provided, return empty dict
    if not oauth_metadata and not oauth_config:
        return {}

    # If no server metadata, return database config as-is
    if not oauth_metadata and oauth_config:
        return oauth_config.copy()

    # If no database config, use server metadata as-is
    if oauth_metadata and not oauth_config:
        return oauth_metadata.copy()

    # Both server metadata and database config exist:
    # start with server metadata, then override with database config fields
    merged_metadata: dict[str, Any] = oauth_metadata.copy()  # type: ignore[union-attr]
    merged_metadata.update(oauth_config)  # type: ignore[arg-type]

    return merged_metadata


def _get_current_utc_time() -> datetime:
    """Get current UTC time. Centralizes datetime.now(timezone.utc) calls."""
    return datetime.now(UTC)


def _convert_tool_list_to_functions(tool_list: list[dict[str, Any]], server_name: str) -> dict[str, Any]:
    """
    Convert tool_list array to toolFunctions object in OpenAI format.

    Example output:
    {
      "tavily_search_mcp_tavilysearchv1": {
        "type": "function",
        "function": {
          "name": "tavily_search_mcp_tavilysearchv1",
          "description": "Search the web",
          "parameters": {
            "type": "object",
            "properties": {...},
            "required": [...]
          }
        },
        "mcpToolName": "tavily_search"
      }
    }
    """
    if not tool_list:
        return {}

    tool_functions = {}
    for tool in tool_list:
        tool_name = tool.get("name")
        if not tool_name:
            continue

        # Create function name with server suffix (mcp_servername format)
        # Normalize: lowercase, replace spaces/hyphens with underscores
        normalized_server = server_name.lower().replace(" ", "_").replace("-", "_")
        function_key = f"{tool_name}_mcp_{normalized_server}"

        tool_functions[function_key] = {
            "type": "function",
            "function": {
                "name": function_key,
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {"type": "object", "properties": {}, "required": []}),
            },
            "mcpToolName": tool_name,  # Store the original MCP tool name
        }

    return tool_functions


def _build_config_from_request(data: ServerCreateRequest, server_name: str = None) -> dict[str, Any]:
    """
    Build config dictionary from ServerCreateRequest

    Important: Registry-specific fields (path, tags, scope, status, numStars, lastConnected, etc.)
    are stored at root level in MongoDB, NOT in config.
    Config stores MCP-specific configuration only (title, description, type, url, oauth, apiKey, etc.)
    """
    # Get all data with DB field names applied
    all_data = data.to_db_dict()

    # Canonical transport source is `type`.
    resolved_transport = all_data.get("type") or "streamable-http"

    # Build MCP-specific configuration (stored in config object)
    config = {
        "title": all_data.get("title"),
        "description": all_data.get("description", ""),
        "type": resolved_transport,
        "url": all_data.get("url"),
        "capabilities": "{}",  # Default empty JSON string
    }

    # Add optional MCP config fields (already mapped to DB field names)
    optional_fields = [
        "timeout",
        "initTimeout",
        "serverInstructions",
        "oauth",
        "customUserVars",
        "headers",
        "requiresOAuth",  # Note: already mapped from requiresOauth
    ]

    for field in optional_fields:
        if field in all_data and all_data[field] is not None:
            config[field] = all_data[field]

    # Convert toolList to toolFunctions in OpenAI format
    if data.toolList is not None:
        use_server_name = server_name or generate_server_name_from_title(data.title)
        config["toolFunctions"] = _convert_tool_list_to_functions(data.toolList, use_server_name)

        # Build tools string (comma-separated tool names)
        tool_names = [tool.get("name", "") for tool in data.toolList if tool.get("name")]
        if tool_names:
            config["tools"] = ", ".join(tool_names)
        else:
            config["tools"] = ""
    else:
        config["toolFunctions"] = {}
        config["tools"] = ""

    # Handle mutually exclusive authentication fields: oauth and apiKey
    # Only store one of them, with oauth taking priority
    if data.oauth is not None:
        # Normalize oauth sub-field keys to snake_case for DB storage
        config["oauth"] = convert_dict_keys_to_snake(config["oauth"])
    elif data.apiKey is not None:
        # When only apiKey is provided, store it
        config["apiKey"] = data.apiKey

    # Always set enabled to False during registration (regardless of frontend input)
    config["enabled"] = False

    return config


def _update_config_from_request(
    config: dict[str, Any], data: ServerUpdateRequest, server_name: str = None
) -> dict[str, Any]:
    """
    Update config dictionary from ServerUpdateRequest

    Important: Only updates MCP-specific config fields.
    Registry fields (path, tags, scope, status) are updated at root level separately.
    Note: enabled field is stored in BOTH config and used to update status at root level.
    """
    # Use to_db_dict() to get DB-compatible field names
    update_dict = data.to_db_dict(exclude_unset=True)

    # Save enabled field separately before removing it (we'll update config with it)
    enabled_value = update_dict.get("enabled")

    # Remove root-level registry fields from update_dict (these are handled at root level)
    # Note: enabled is removed here but will be added to config separately
    registry_fields = [
        "path",
        "tags",
        "status",
        "serverName",
        "numStars",
        "enabled",
    ]
    for field in registry_fields:
        update_dict.pop(field, None)

    # Handle mutually exclusive authentication fields: oauth and apiKey
    if "oauth" in update_dict or "apiKey" in update_dict:
        existing_oauth = config.pop("oauth", {})
        existing_apikey = config.pop("apiKey", {})
        if "oauth" in update_dict:
            oauth_update = update_dict.pop("oauth")
            # If oauth is explicitly None, remove it (DCR - Direct Connection Required)
            if oauth_update is None:
                pass
            # Otherwise, merge/update oauth config
            elif oauth_update:
                # Normalize incoming camelCase keys to snake_case before merging/storing
                oauth_update = convert_dict_keys_to_snake(oauth_update)
                # Merge new oauth with existing oauth (upsert operation)
                if isinstance(existing_oauth, dict) and isinstance(oauth_update, dict):
                    existing_oauth.update(oauth_update)
                    config["oauth"] = existing_oauth
                else:
                    config["oauth"] = oauth_update

        if "apiKey" in update_dict:
            apikey_update = update_dict.pop("apiKey")

            # Only save if not None and not empty
            if apikey_update:
                if isinstance(existing_apikey, dict) and isinstance(apikey_update, dict):
                    existing_apikey.update(apikey_update)
                    config["apiKey"] = existing_apikey
                else:
                    config["apiKey"] = apikey_update

    if "headers" in update_dict:
        headers_update = update_dict.pop("headers")
        if headers_update:
            config["headers"] = headers_update
        else:
            config.pop("headers", None)

    # Update config with MCP-specific fields only
    # Note: Field names are already mapped to DB names via to_db_dict()
    mcp_config_fields = [
        "title",
        "url",
        "description",
        "type",
        "timeout",
        "initTimeout",
        "serverInstructions",
        "requiresOAuth",  # Already mapped from requiresOauth
        "oauth",
        "customUserVars",
        # Note: toolList is handled separately below, not directly stored in config
    ]
    for key, value in update_dict.items():
        if key in mcp_config_fields and value is not None:
            config[key] = value

    # Update enabled field in config if provided
    if enabled_value is not None:
        config["enabled"] = enabled_value

    # If toolList is updated, regenerate toolFunctions and tools string
    if "toolList" in update_dict and update_dict["toolList"] is not None:
        tool_list = update_dict["toolList"]

        # Convert to toolFunctions format
        if server_name:
            config["toolFunctions"] = _convert_tool_list_to_functions(tool_list, server_name)

        # Generate tools string
        tool_names = [tool.get("name", "") for tool in tool_list if tool.get("name")]
        if tool_names:
            config["tools"] = ", ".join(tool_names)
        else:
            config["tools"] = ""

    return config


class ServerServiceV1:
    """Service class for managing MCP servers with MongoDB"""

    def __init__(
        self,
        user_service: UserService,
        token_service: TokenService,
        oauth_service: Any,
        mcp_server_repo: MCPServerRepository,
    ):
        """Initialize server service with search index manager."""
        self.mcp_server_repo = mcp_server_repo
        self.user_service = user_service
        self.token_service = token_service
        self.oauth_service = oauth_service
        logger.info("ServerServiceV1 initialized with search index manager")

    async def list_servers(
        self,
        query: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20,
        user_id: str | None = None,
        accessible_server_ids: list[str] | None = None,
    ) -> tuple[list[ExtendedMCPServer], int]:
        """
        List servers with filtering and pagination.

        Args:
            query: Free-text search across server_name, description, tags
            status: Filter by operational state (active, inactive, error)
            page: Page number (min: 1)
            per_page: Items per page (min: 1, max: 100)
            user_id: Current user's ID (kept for compatibility but not used for filtering)
            accessible_server_ids: List of server ID strings the user has VIEW access to.

        Returns:
            Tuple of (servers list, total count)
        """
        # Validate and sanitize pagination
        page = max(1, page)
        per_page = max(1, min(100, per_page))
        skip = (page - 1) * per_page

        # Build filter conditions
        filters = []

        # Status filter (now at root level)
        if status:
            filters.append({"status": status})

        # Text search across multiple fields (tags now at root level)
        if query:
            query_lower = query.lower()
            text_filter = {
                "$or": [
                    {"serverName": {"$regex": query, "$options": "i"}},
                    {"config.description": {"$regex": query, "$options": "i"}},
                    {"tags": query_lower},
                ]
            }
            filters.append(text_filter)

        # Access control filter (only applied when caller supplies an explicit list)
        if accessible_server_ids is not None:
            object_ids = [PydanticObjectId(sid) for sid in accessible_server_ids]
            filters.append({"_id": {"$in": object_ids}})

        # Combine all filters
        if filters:
            query_filter = {"$and": filters} if len(filters) > 1 else filters[0]
        else:
            query_filter = {}

        # Execute query with pagination
        total = await ExtendedMCPServer.find(query_filter).count()
        servers = (
            await ExtendedMCPServer.find(query_filter).sort([("createdAt", -1)]).skip(skip).limit(per_page).to_list()
        )
        return servers, total

    async def get_server_by_id(
        self,
        server_id: str,
        user_id: str | None = None,
    ) -> ExtendedMCPServer | None:
        """
        Get a server by its ID.

        Args:
            server_id: Server document ID
            user_id: Current user's ID (kept for compatibility but not used)

        Returns:
            Server document or None if not found
        """
        try:
            # Convert string ID to ObjectId
            obj_id = PydanticObjectId(server_id)
        except Exception:
            logger.warning(f"Invalid server ID format: {server_id}")
            return None

        server = await ExtendedMCPServer.get(obj_id)

        return server

    async def get_server_by_path(self, path: str) -> ExtendedMCPServer | None:
        """
        Get a server by its path.

        Args:
            path: Server path (e.g., /github)

        Returns:
            Server document or None if not found
        """
        return await ExtendedMCPServer.find_one({"path": path})

    async def create_server(
        self,
        data: ServerCreateRequest,
        user_id: str,
        skip_post_registration_checks: bool = False,
    ) -> ExtendedMCPServer:
        """
        Create a new server.

        Args:
            data: Server creation data
            user_id: ID of the user creating the server

        Returns:
            Created server document

        Raises:
            ValueError: If path+url combination already exists, server_name already exists, or tags contain duplicates (case-insensitive)
        """
        session = get_current_session()
        # Check if path+url combination already exists
        # Only reject if BOTH path AND url are the same (to allow same path for different services)
        existing_servers = await ExtendedMCPServer.find({"path": data.path}, session=session).to_list()
        for existing in existing_servers:
            existing_url = existing.config.get("url") if existing.config else None
            if existing_url == data.url:
                raise ValueError(f"Server with path '{data.path}' and URL '{data.url}' already exists")

        # Check if serverName already exists
        server_name = generate_server_name_from_title(data.title)
        existing_name = await ExtendedMCPServer.find_one({"serverName": server_name}, session=session)
        if existing_name:
            raise ValueError(f"Server with name '{server_name}' already exists")

        # Check for duplicate tags (case-insensitive)
        normalized_tags = [tag.lower() for tag in data.tags]
        if len(normalized_tags) != len(set(normalized_tags)):
            raise ValueError("Duplicate tags are not allowed (case-insensitive)")

        # Build MCP config dictionary (MCP-specific fields only)
        config = _build_config_from_request(data, server_name=server_name)

        # Encrypt sensitive authentication fields before storing
        config = encrypt_auth_fields(config)

        # Calculate numTools from toolFunctions
        tool_functions = config.get("toolFunctions", {})
        num_tools = len(tool_functions) if tool_functions else 0

        # Get author user reference - authentication required
        author = await self.user_service.get_user_by_user_id(user_id)

        if not author:
            raise ValueError(f"Authentication required: User {user_id} not found")

        # Create server document with registry fields at root level
        now = _get_current_utc_time()
        server = ExtendedMCPServer(
            serverName=server_name,
            config=config,
            author=author.id,  # Use PydanticObjectId instead of Link
            # Registry-specific root-level fields
            path=data.path,
            tags=[tag.lower() for tag in data.tags],  # Normalize tags to lowercase
            status="active",  # Default status (independent of enabled field)
            numTools=num_tools,  # Store calculated numTools at root level
            numStars=data.numStars,
            # Initialize error tracking fields as None
            lastError=None,
            errorMessage=None,
            # Timestamps
            createdAt=now,
            updatedAt=now,
        )

        await server.insert(session=session)
        logger.info(f"Created server: {server.serverName} (ID: {server.id}, Path: {data.path})")

        # Perform health check and tool retrieval after registration
        if data.url and not skip_post_registration_checks:
            logger.info(f"Performing post-registration health check and tool retrieval for {server.serverName}")

            try:
                # 1. Health check - REQUIRED
                from registry.core.mcp_client import perform_health_check

                config = server.config or {}
                url = config.get("url")
                transport = config.get("type", "streamable-http")

                (
                    is_healthy,
                    status_msg,
                    response_time_ms,
                    _,
                ) = await perform_health_check(
                    url=url,
                    transport=transport,
                )
                logger.info(
                    f"Health check result for {server.serverName}: {status_msg} (response_time: {response_time_ms}ms)"
                )

                if not is_healthy:
                    # Health check failed - delete the server and reject registration
                    logger.error(f"Health check failed for {server.serverName}: {status_msg}")
                    await server.delete(session=session)
                    raise ValueError(f"Server registration rejected: Health check failed - {status_msg}")

                # Update server with health check results (root-level field)
                server.lastConnected = _get_current_utc_time()
                server.status = "active"

                # 2. Retrieve capabilities (but skip tools - they will be fetched on-demand)
                logger.info(f"Retrieving capabilities for {server.serverName} (skipping tools)")

                # Initialize empty toolFunctions and tools (will be populated on first use)
                config["toolFunctions"] = {}
                config["tools"] = ""

                # Try to get capabilities only
                try:
                    # Build server_info dict for mcp_client
                    server_info = _build_server_info_for_mcp_client(config, server.tags)

                    from registry.core.mcp_client import (
                        get_tools_and_capabilities_from_server,
                    )

                    # Get tools, resources, prompts, and capabilities (we'll only use capabilities for now)
                    result = await get_tools_and_capabilities_from_server(
                        data.url,
                        server_info,
                        include_resources=True,
                        include_prompts=True,
                    )

                    # Save capabilities if retrieved successfully
                    if result.capabilities:
                        config["capabilities"] = json.dumps(result.capabilities)
                        logger.info(f"Saved capabilities for {server.serverName}: {config['capabilities']}")
                    else:
                        config["capabilities"] = "{}"
                        logger.warning(f"No capabilities retrieved for {server.serverName}, using empty JSON")

                    # Store resources and prompts (empty lists if not retrieved)
                    config["resources"] = result.resources or []
                    config["prompts"] = result.prompts or []
                    logger.info(
                        f"Saved {len(result.resources or [])} resources and {len(result.prompts or [])} prompts for {server.serverName}"
                    )

                except Exception as e:
                    # If capabilities retrieval fails, just use empty capabilities
                    config["capabilities"] = "{}"
                    logger.warning(f"Failed to retrieve capabilities for {server.serverName}: {e}")

                logger.info(f"Server {server.serverName} registered successfully. Tools will be fetched on-demand.")

                if data.requiresOauth:
                    logger.info(f"OAuth configuration detected for {server.serverName}, retrieving OAuth metadata...")

                    oauth_metadata = await get_oauth_metadata_from_server(data.url)

                    if oauth_metadata:
                        config["oauthMetadata"] = oauth_metadata
                        logger.info(f"Saved raw OAuth metadata for {server.serverName}: {json.dumps(oauth_metadata)}")
                    else:
                        # Save empty oauthMetadata if retrieval failed
                        config["oauthMetadata"] = {}
                        logger.info(
                            f"No OAuth metadata available for {server.serverName} (server may not support OAuth autodiscovery), saved empty oauthMetadata"
                        )

                # Update numTools at root level (0 since tools not fetched yet)
                server.numTools = 0

                # Save updated server
                server.config = config
                server.updatedAt = _get_current_utc_time()
                await server.save(session=session)
            except ValueError:
                # Re-raise ValueError (our validation errors)
                raise
            except Exception as e:
                # Unexpected error during health check or tool retrieval
                logger.error(
                    "Unexpected error during post-registration health check and tool "
                    "retrieval for server %s (ID: %s, Path: %s): %s",
                    server.serverName,
                    server.id,
                    server.path,
                    str(e),
                    exc_info=True,
                )
                await server.delete(session=session)
        return server

    def _schedule_vector_sync(self, server: ExtendedMCPServer, old_hash: str | None) -> None:
        """Schedule vector sync or metadata-only update based on content hash change."""
        if server.vectorContentHash != old_hash:
            asyncio.create_task(self.mcp_server_repo.sync_to_vector_db(server, is_delete=True))
        else:
            asyncio.create_task(
                self.mcp_server_repo.update_entity_metadata(
                    "server_id",
                    str(server.id),
                    server.mutable_metadata(),
                )
            )

    async def update_server(
        self,
        server_id: str,
        data: ServerUpdateRequest,
        user_id: str | None = None,
    ) -> ExtendedMCPServer:
        """
        Update a server.

        Args:
            server_id: Server document ID
            data: Update data (partial)
            user_id: Current user's ID (kept for compatibility but not used)

        Returns:
            Updated server document

        Raises:
            ValueError: If server not found
        """
        session = get_current_session()
        server = await self.get_server_by_id(server_id, user_id)

        if not server:
            raise ValueError("Server not found")

        # Get current config
        config = server.config or {}

        # Check for duplicate tags if tags are being updated
        if data.tags is not None:
            normalized_tags = [tag.lower() for tag in data.tags]
            if len(normalized_tags) != len(set(normalized_tags)):
                raise ValueError("Duplicate tags are not allowed (case-insensitive)")

        # Update root-level registry fields
        if data.path is not None:
            server.path = data.path
        if data.tags is not None:
            server.tags = [tag.lower() for tag in data.tags]
        if data.status is not None:
            server.status = data.status

        # Update config with MCP-specific values only
        updated_config = _update_config_from_request(config, data, server_name=server.serverName)

        # Encrypt sensitive authentication fields if they were updated
        if data.oauth is not None or data.apiKey is not None:
            updated_config = encrypt_auth_fields(updated_config)

        if data.requiresOauth:
            logger.info(f"OAuth configuration detected for {server.serverName}, retrieving OAuth metadata...")
            oauth_metadata = await get_oauth_metadata_from_server(data.url)
            if oauth_metadata:
                updated_config["oauthMetadata"] = oauth_metadata
                logger.info(f"Saved raw OAuth metadata for {server.serverName}: {json.dumps(oauth_metadata)}")
            else:
                # Save empty oauthMetadata if retrieval failed
                updated_config["oauthMetadata"] = {}
                logger.info(
                    f"No OAuth metadata available for {server.serverName} (server may not support OAuth autodiscovery), saved empty oauthMetadata"
                )

        # If toolFunctions was updated, recalculate numTools
        if "toolFunctions" in updated_config:
            tool_functions = updated_config.get("toolFunctions", {})
            server.numTools = len(tool_functions) if tool_functions else 0

        server.config = updated_config

        # Update the updatedAt timestamp
        server.updatedAt = _get_current_utc_time()

        old_hash = server.vectorContentHash
        await server.save(session=session)

        self._schedule_vector_sync(server, old_hash)
        return server

    async def delete_server(
        self,
        server_id: str,
        user_id: str | None = None,
    ) -> bool:
        """
        Delete a server.

        Args:
            server_id: Server document ID
            user_id: Current user's ID (kept for compatibility but not used)

        Returns:
            True if deleted successfully

        Raises:
            ValueError: If server not found
        """
        try:
            obj_id = PydanticObjectId(server_id)
        except Exception:
            raise ValueError("Server not found")

        session = get_current_session()
        server = await ExtendedMCPServer.get(obj_id, session=session)

        if not server:
            raise ValueError("Server not found")

        # Remove from vector DB before deleting from MongoDB (background task)
        asyncio.create_task(self.mcp_server_repo.delete_by_server_id(server_id, server.serverName))
        await server.delete(session=session)
        logger.info(f"Deleted server: {server.serverName} (ID: {server.id})")
        return True

    async def _fetch_and_update_tools(
        self,
        server: ExtendedMCPServer,
        user_id: str | None = None,
    ) -> bool:
        """
        Fetch tools, resources, and prompts from server and update config.

        Args:
            server: Server document
            user_id: User ID (required for OAuth servers)

        Returns:
            True if tools were successfully fetched and updated, False otherwise
        """
        # Use consolidated retrieve_from_server which handles both OAuth and apiKey
        logger.info(f"Fetching tools, resources, and prompts for server {server.serverName}")
        (
            tool_list,
            resource_list,
            prompt_list,
            _,
            error_msg,
        ) = await self.retrieve_from_server(
            server=server,
            include_capabilities=False,
            include_resources=True,
            include_prompts=True,
            user_id=user_id,
        )

        if tool_list:
            # Convert tool_list to toolFunctions format
            tool_functions = _convert_tool_list_to_functions(tool_list, server.serverName)

            # Update config with toolFunctions (full replacement)
            server.config["toolFunctions"] = tool_functions

            # Update tools string (comma-separated tool names)
            tool_names = [tool.get("name", "") for tool in tool_list if tool.get("name")]
            server.config["tools"] = ", ".join(tool_names) if tool_names else ""

            # Update numTools at root level
            server.numTools = len(tool_functions)

            # Store resources and prompts in config
            server.config["resources"] = resource_list or []
            server.config["prompts"] = prompt_list or []

            logger.info(
                f"Successfully fetched and updated {len(tool_functions)} tools, {len(resource_list or [])} resources, {len(prompt_list or [])} prompts for {server.serverName}"
            )
            return True
        else:
            logger.warning(f"Failed to fetch tools for {server.serverName}: {error_msg}")
            return False

    async def toggle_server_status(
        self,
        server_id: str,
        enabled: bool,
        user_id: str | None = None,
    ) -> ExtendedMCPServer:
        """
        Toggle server enabled/disabled status.
        When enabling, fetch tools and sync to vector DB (upsert).
        When disabling, remove from vector DB.

        Args:
            server_id: Server document ID
            enabled: Enable (True) or disable (False)
            user_id: Current user's ID (required for OAuth servers)

        Returns:
            Updated server document

        Raises:
            ValueError: If server not found or user_id missing for OAuth server
        """
        server = await self.get_server_by_id(server_id, user_id)

        if not server:
            raise ValueError("Server not found")

        # Update enabled field in config
        if server.config is None:
            server.config = {}
        server.config["enabled"] = enabled

        # If enabling the server, fetch tools and update toolFunctions
        if enabled:
            success = await self._fetch_and_update_tools(server, user_id)
            if not success:
                # Rollback enabled status
                server.config["enabled"] = False
                await server.save()
                raise ValueError("Failed to fetch tools from server. Server remains disabled.")

        # Update the updatedAt timestamp
        server.updatedAt = _get_current_utc_time()

        old_hash = server.vectorContentHash
        await server.save()
        logger.info(f"Toggled server {server.serverName} (ID: {server.id}) enabled to {enabled}")

        self._schedule_vector_sync(server, old_hash)
        return server

    async def get_server_tools(
        self,
        server_id: str,
        user_id: str | None = None,
    ) -> tuple[ExtendedMCPServer, dict[str, Any]]:
        """
        Get server tools in toolFunctions format.

        Args:
            server_id: Server document ID
            user_id: Current user's ID (kept for compatibility but not used)

        Returns:
            Tuple of (server, toolFunctions dict)

        Raises:
            ValueError: If server not found
        """
        server = await self.get_server_by_id(server_id, user_id)

        if not server:
            raise ValueError("Server not found")

        tool_functions = _extract_config_field(server, "toolFunctions", {})
        return server, tool_functions

    @track_tool_discovery
    async def retrieve_from_server(
        self,
        server: ExtendedMCPServer,
        include_capabilities: bool = True,
        include_resources: bool = True,
        include_prompts: bool = True,
        user_id: str | None = None,
    ) -> tuple[
        list[dict[str, Any]] | None,
        list[dict[str, Any]] | None,
        list[dict[str, Any]] | None,
        dict[str, Any] | None,
        str | None,
    ]:
        """
        Consolidated method to retrieve tools, resources, prompts, and optionally capabilities from a server.
        Args:
            server: Server document
            include_capabilities: Whether to retrieve capabilities (default: True)
            include_resources: Whether to retrieve resources (default: True)
            include_prompts: Whether to retrieve prompts (default: True)
            user_id: User ID for OAuth token retrieval (required for OAuth servers)

        Returns:
            Tuple of (tool_list, resource_list, prompt_list, capabilities_dict, error_message)
            - If successful: (tool_list, resource_list, prompt_list, capabilities_dict, None)
            - If failed: (None, None, None, None, error_message)
            - If include_capabilities=False: (tool_list, resource_list, prompt_list, None, None) or (None, None, None, None, error_message)
        """
        config = server.config or {}
        url = config.get("url")

        if not url:
            return None, None, None, None, "No URL configured"

        # Check if server requires OAuth
        has_oauth = config.get("oauth") is not None

        if has_oauth and not user_id:
            return (
                None,
                None,
                None,
                None,
                "OAuth server requires user_id for token retrieval",
            )

        # Get transport type
        transport_type = config.get("type", "streamable-http")

        try:
            # Build complete headers with all authentication (OAuth, apiKey, custom)
            # This consolidates all auth logic in one place
            try:
                headers = await build_complete_headers_for_server(
                    self.oauth_service,
                    server,
                    user_id,
                )
            except OAuthReAuthRequiredError as e:
                # OAuth re-authentication needed - return special error format
                return (
                    None,
                    None,
                    None,
                    None,
                    f"oauth_required:{e.auth_url or str(e)}",
                )
            except (OAuthTokenError, MissingUserIdError) as e:
                # OAuth token errors or missing user ID
                return None, None, None, None, f"Authentication error: {str(e)}"
            except AuthenticationError as e:
                # Other authentication errors
                return None, None, None, None, f"Authentication error: {str(e)}"

            items_to_retrieve = []
            if include_capabilities:
                items_to_retrieve.append("capabilities")
            items_to_retrieve.append("tools")
            if include_resources:
                items_to_retrieve.append("resources")
            if include_prompts:
                items_to_retrieve.append("prompts")

            logger.info(f"Retrieving {', '.join(items_to_retrieve)} from {url} for server {server.serverName}")

            # Use the mcp_client with pre-built headers (pure transport layer)
            from registry.core.mcp_client import (
                get_tools_and_capabilities_from_server,
            )

            result = await get_tools_and_capabilities_from_server(
                url,
                headers=headers,
                transport_type=transport_type,
                include_resources=include_resources,
                include_prompts=include_prompts,
            )
            server.config["requiresInit"] = bool(result.requires_init)

            if include_capabilities:
                if result.tools is None or result.capabilities is None:
                    error_msg = result.error_message or "Failed to retrieve tools and capabilities from MCP server"
                    logger.warning(f"{error_msg} for {server.serverName}")
                    return None, None, None, None, error_msg

                logger.info(
                    f"Retrieved {len(result.tools)} tools, {len(result.resources or [])} resources, {len(result.prompts or [])} prompts, and capabilities from {server.serverName}"
                )
                return (
                    result.tools,
                    result.resources,
                    result.prompts,
                    result.capabilities,
                    None,
                )
            else:
                if result.tools is None:
                    error_msg = result.error_message or "Failed to retrieve tools from MCP server"
                    logger.warning(f"{error_msg} for {server.serverName}")
                    return None, None, None, None, error_msg

                logger.info(
                    f"Retrieved {len(result.tools)} tools, {len(result.resources or [])} resources, {len(result.prompts or [])} prompts from {server.serverName}"
                )
                return result.tools, result.resources, result.prompts, None, None

        except Exception as e:
            error_msg = f"Error: {type(e).__name__} - {str(e)}"
            logger.error(f"Retrieval error for server {server.serverName}: {e}")
            return None, None, None, None, error_msg

    async def retrieve_tools_and_capabilities_from_server(
        self,
        server: ExtendedMCPServer,
        user_id: str | None = None,
    ) -> tuple[
        list[dict[str, Any]] | None,
        list[dict[str, Any]] | None,
        list[dict[str, Any]] | None,
        dict[str, Any] | None,
        str | None,
    ]:
        """
        Retrieve tools, resources, prompts, and capabilities from a server using the MCP client.

        This is a best-effort attempt - failures are logged but don't prevent registration.
        Tools can be fetched on-demand later.

        Args:
            server: Server document
            user_id: User ID for OAuth token retrieval (required for OAuth servers)

        Returns:
            Tuple of (tool_list, resource_list, prompt_list, capabilities_dict, error_message)
            - If successful, returns (tool_list, resource_list, prompt_list, capabilities_dict, None)
            - If failed, returns (None, None, None, None, error_message)
            - Empty results are acceptable for registration
        """
        return await self.retrieve_from_server(
            server,
            include_capabilities=True,
            include_resources=True,
            include_prompts=True,
            user_id=user_id,
        )

    async def refresh_server_health(
        self,
        server_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Refresh server health status.

        Uses the same strict validation as registration:
        - Retrieves tools and capabilities from the server
        - If capabilities cannot be retrieved, server is considered unhealthy
        - This serves as a comprehensive sanity check

        Args:
            server_id: Server document ID
            user_id: Current user's ID (kept for compatibility but not used)

        Returns:
            Health information dictionary

        Raises:
            ValueError: If server not found
        """
        server = await self.get_server_by_id(server_id, user_id)

        if not server:
            raise ValueError("Server not found")

        now = _get_current_utc_time()

        # Use the same validation as registration: retrieve tools, resources, prompts, and capabilities
        # This is a more comprehensive health check than just HTTP GET
        (
            tool_list,
            resource_list,
            prompt_list,
            capabilities,
            tool_error,
        ) = await self.retrieve_tools_and_capabilities_from_server(server, user_id)

        if tool_list is None or capabilities is None:
            # Health check failed - cannot retrieve capabilities
            logger.error(f"Health check failed for {server.serverName}: {tool_error}")

            server.status = "error"
            server.lastError = now
            server.errorMessage = tool_error or "Failed to retrieve capabilities"
            server.lastConnected = now
            server.updatedAt = now

            await server.save()

            return {
                "server": server,
                "status": "unhealthy",
                "status_message": tool_error or "Failed to retrieve capabilities",
                "last_checked": now,
                "response_time_ms": None,
            }

        # Health check passed - capabilities retrieved successfully
        logger.info(
            f"Health check passed for {server.serverName}: retrieved {len(tool_list)} tools, {len(resource_list or [])} resources, {len(prompt_list or [])} prompts, and capabilities"
        )

        server.status = "active"
        server.lastError = None
        server.errorMessage = None
        server.lastConnected = now
        server.updatedAt = now

        # Update capabilities, tools, resources, and prompts in config
        config = server.config or {}
        if capabilities:
            config["capabilities"] = json.dumps(capabilities)

        # Update toolFunctions if tools were retrieved
        if tool_list:
            # Convert tool_list to toolFunctions format
            tool_functions = _convert_tool_list_to_functions(tool_list, server.serverName)
            config["toolFunctions"] = tool_functions

            # Update tools string (comma-separated tool names)
            tool_names = [tool.get("name", "") for tool in tool_list if tool.get("name")]
            config["tools"] = ", ".join(tool_names) if tool_names else ""

            # Update numTools at root level
            server.numTools = len(tool_functions)
            logger.info(f"Updated {len(tool_functions)} tools for {server.serverName} during health refresh")

        # Store resources and prompts
        config["resources"] = resource_list or []
        config["prompts"] = prompt_list or []
        logger.info(
            f"Updated {len(resource_list or [])} resources and {len(prompt_list or [])} prompts for {server.serverName} during health refresh"
        )

        server.config = config
        await server.save()

        # Return health info
        return {
            "server": server,
            "status": "healthy",
            "status_message": f"healthy (retrieved {len(tool_list)} tools, {len(resource_list or [])} resources, {len(prompt_list or [])} prompts)",
            "last_checked": now,
            "response_time_ms": None,  # We don't track response time for MCP connections
        }

    async def get_stats(self) -> dict[str, Any]:
        """
        Get system-wide statistics (Admin only).

        This method uses MongoDB aggregation pipelines to gather statistics about:
        - Servers (total, by scope, by status, by transport)
        - Tokens (total, by type, active/expired)
        - Active users (users with active tokens)
        - Total tools across all servers

        Returns:
            Dictionary containing all statistics
        """

        stats = {}

        # 1. Server Statistics
        try:
            # Use facet to get multiple aggregations in one query
            # Note: scope and status are now at root level
            server_pipeline = [
                {
                    "$facet": {
                        "total": [{"$count": "count"}],
                        "by_status": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}],
                        "by_transport": [{"$group": {"_id": "$config.type", "count": {"$sum": 1}}}],
                        "total_tools": [
                            {
                                "$addFields": {
                                    "toolCount": {
                                        "$cond": {
                                            "if": {"$isArray": "$config.tool_list"},
                                            "then": {"$size": "$config.tool_list"},
                                            "else": 0,
                                        }
                                    }
                                }
                            },
                            {"$group": {"_id": None, "total": {"$sum": "$toolCount"}}},
                        ],
                    }
                }
            ]

            # Use Beanie's aggregate method directly
            server_results = await ExtendedMCPServer.aggregate(server_pipeline).to_list()

            if server_results and len(server_results) > 0:
                result = server_results[0]

                # Total servers
                stats["total_servers"] = result["total"][0]["count"] if result["total"] else 0

                # Servers by status
                servers_by_status = {}
                for item in result.get("by_status", []):
                    status = item["_id"] or "unknown"
                    servers_by_status[status] = item["count"]
                stats["servers_by_status"] = servers_by_status

                # Servers by transport
                servers_by_transport = {}
                for item in result.get("by_transport", []):
                    transport = item["_id"] or "unknown"
                    servers_by_transport[transport] = item["count"]
                stats["servers_by_transport"] = servers_by_transport

                # Total tools
                stats["total_tools"] = result["total_tools"][0]["total"] if result["total_tools"] else 0
            else:
                # No servers found
                stats["total_servers"] = 0
                stats["servers_by_status"] = {}
                stats["servers_by_transport"] = {}
                stats["total_tools"] = 0

        except Exception as e:
            logger.exception(f"Error gathering server statistics: {e}")
            stats["total_servers"] = 0
            stats["servers_by_scope"] = {}
            stats["servers_by_status"] = {}
            stats["servers_by_transport"] = {}
            stats["total_tools"] = 0

        # 2. Token Statistics
        try:
            now = _get_current_utc_time()

            token_pipeline = [
                {
                    "$facet": {
                        "total": [{"$count": "count"}],
                        "by_type": [{"$group": {"_id": "$type", "count": {"$sum": 1}}}],
                        "by_expiry": [
                            {
                                "$group": {
                                    "_id": {
                                        "$cond": [
                                            {"$gt": ["$expiresAt", now]},
                                            "active",
                                            "expired",
                                        ]
                                    },
                                    "count": {"$sum": 1},
                                }
                            }
                        ],
                    }
                }
            ]

            # Use Beanie's aggregate method directly
            token_results = await Token.aggregate(token_pipeline).to_list()

            if token_results and len(token_results) > 0:
                result = token_results[0]

                # Total tokens
                stats["total_tokens"] = result["total"][0]["count"] if result["total"] else 0

                # Tokens by type
                tokens_by_type = {}
                for item in result.get("by_type", []):
                    token_type = item["_id"] or "unknown"
                    tokens_by_type[token_type] = item["count"]
                stats["tokens_by_type"] = tokens_by_type

                # Active/Expired tokens
                active_count = 0
                expired_count = 0
                for item in result.get("by_expiry", []):
                    if item["_id"] == "active":
                        active_count = item["count"]
                    elif item["_id"] == "expired":
                        expired_count = item["count"]

                stats["active_tokens"] = active_count
                stats["expired_tokens"] = expired_count
            else:
                # No tokens found
                stats["total_tokens"] = 0
                stats["tokens_by_type"] = {}
                stats["active_tokens"] = 0
                stats["expired_tokens"] = 0

        except Exception as e:
            logger.exception(f"Error gathering token statistics: {e}")
            stats["total_tokens"] = 0
            stats["tokens_by_type"] = {}
            stats["active_tokens"] = 0
            stats["expired_tokens"] = 0

        # 3. Active Users Statistics
        try:
            now = _get_current_utc_time()

            # Count unique users with active tokens
            active_users_pipeline = [
                {"$match": {"expiresAt": {"$gt": now}}},
                {"$group": {"_id": "$userId"}},
                {"$count": "count"},
            ]

            # Use Beanie's aggregate method directly
            active_users_results = await Token.aggregate(active_users_pipeline).to_list()

            stats["active_users"] = active_users_results[0]["count"] if active_users_results else 0

        except Exception as e:
            logger.exception(f"Error gathering active users statistics: {e}")
            stats["active_users"] = 0

        logger.info(
            f"Generated system statistics: {stats['total_servers']} servers,"
            f" {stats['total_tokens']} tokens, {stats['active_users']} active users"
        )

        return stats
