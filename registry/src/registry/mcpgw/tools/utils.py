"""
Dynamic MCP server proxy routes.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from httpx_sse import ServerSentEvent
from mcp.server.session import ServerSession
from mcp.shared.session import RequestId
from mcp.types import (
    CancelledNotification,
    ElicitCompleteNotification,
    LoggingMessageNotification,
    ProgressNotification,
    PromptListChangedNotification,
    ResourceListChangedNotification,
    ResourceUpdatedNotification,
    TaskStatusNotification,
    ToolListChangedNotification,
)
from pydantic import ValidationError

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

from ...auth.dependencies import UserContextDict, effective_scopes_from_context
from ...auth.oauth.flow_state_manager import FlowStateManager
from ...auth.oauth.types import StateMetadata
from ...core.exceptions import InternalServerException, UrlElicitationRequiredException
from ...schemas.errors import AuthenticationError, OAuthReAuthRequiredError, OAuthTokenError
from ...services.server_service import build_complete_headers_for_server

if TYPE_CHECKING:
    from ...services.oauth.oauth_service import MCPOAuthService

logger = logging.getLogger(__name__)


class NotificationMethod(StrEnum):
    CANCELLED = "notifications/cancelled"
    PROGRESS = "notifications/progress"
    LOGGING_MESSAGE = "notifications/message"
    RESOURCE_UPDATED = "notifications/resources/updated"
    RESOURCE_LIST_CHANGED = "notifications/resources/list_changed"
    TOOL_LIST_CHANGED = "notifications/tools/list_changed"
    PROMPT_LIST_CHANGED = "notifications/prompts/list_changed"
    ELICITATION_COMPLETE = "notifications/elicitation/complete"
    TASK_STATUS = "notifications/tasks/status"


async def forward_notification(session: ServerSession, obj: dict, *, related_request_id: RequestId | None) -> None:
    """
    Use the ServerSession.send_notification() method to **schedule** the forwarding of a notification from
    downstream MPC servers to our client. Delivery is best effort - if client doesn't make an SSE GET connection
    or doesn't make that connection in time, notification might be lost.

    Args:
        session: ServerSession
        obj: The parsed "data" field of a Server-Sent Event. According to MCP spec it must be of type dict[str, Any]
        related_request_id: The client request ID that the notification is related to. Optional

    Returns: None

    Raises: Nothing. All exceptions are caught and logged only, as notification forwarding is best-effort anyway.
    """

    if "method" not in obj or not isinstance(obj["method"], str):
        logger.error(f"unexpected notification data: {str(obj)}")

        return

    try:
        match obj["method"]:
            case NotificationMethod.CANCELLED:
                notification: Any = CancelledNotification.model_validate(obj)
            case NotificationMethod.PROGRESS:
                notification = ProgressNotification.model_validate(obj)
            case NotificationMethod.LOGGING_MESSAGE:
                notification = LoggingMessageNotification.model_validate(obj)
            case NotificationMethod.RESOURCE_UPDATED:
                notification = ResourceUpdatedNotification.model_validate(obj)
            case NotificationMethod.RESOURCE_LIST_CHANGED:
                notification = ResourceListChangedNotification.model_validate(obj)
            case NotificationMethod.TOOL_LIST_CHANGED:
                notification = ToolListChangedNotification.model_validate(obj)
            case NotificationMethod.PROMPT_LIST_CHANGED:
                notification = PromptListChangedNotification.model_validate(obj)
            case NotificationMethod.ELICITATION_COMPLETE:
                notification = ElicitCompleteNotification.model_validate(obj)
            case NotificationMethod.TASK_STATUS:
                notification = TaskStatusNotification.model_validate(obj)
            case _:
                logger.error(f"unexpected notification data: {str(obj)}")

                return
    except ValidationError as exc:
        logger.error(f"error parsing notification data: {str(exc)}")

        return

    try:
        await session.send_notification(notification, related_request_id)
    except Exception:
        logger.error(f"encountered error while scheduling notification-forwarding to client: {str}")

        return


def parse_data_field(
    event: ServerSentEvent,
) -> tuple[dict, Literal["notification", "response", "irrelevant"]]:
    """
    Parse the Server-Send Event and check if it's a notification, a response, or something we don't care about.
    Reference: https://modelcontextprotocol.io/specification/2025-11-25/schema?search=server-sent+event#json-rpc

    Args:
        event: httpx_sse.ServerSendEvent

    Returns:
        1st: A Python dictionary deserialized from the "data" field. An empty dictionary if event if malformed
            or of a kind we don't care about.
        2nd: One of three string values indicating if the event **seems like** a notification, a response,
            or **is** something irrelevant.

    Raises: Nothing
    """
    if event.event != "message":
        return {}, "irrelevant"

    try:
        obj = event.json()
    except Exception:
        return {}, "irrelevant"

    if not isinstance(obj, dict):
        return {}, "irrelevant"

    if "jsonrpc" not in obj or obj["jsonrpc"] != "2.0":
        return {}, "irrelevant"

    if ("result" in obj and isinstance(obj["result"], dict)) or ("error" in obj and isinstance(obj["error"], dict)):
        return obj, "response"
    elif "method" in obj and isinstance(obj["method"], str) and obj["method"].startswith("notifications/"):
        return obj, "notification"
    else:
        return {}, "irrelevant"


async def build_authenticated_headers(
    oauth_service: MCPOAuthService,
    server: ExtendedMCPServer,
    auth_context: UserContextDict,
    additional_headers: dict[str, str] | None = None,
    *,
    state_metadata: StateMetadata | None = None,
    agentcore_auth_service=None,
    redis_client=None,
) -> dict[str, str]:
    """
    Build complete headers with authentication for MCP server requests.
    Consolidates auth logic used by all proxy endpoints.

    Supports multiple authentication types:
    - AgentCore Runtime: JWT/IAM authentication for federated AgentCore MCP servers (with caching)
    - OAuth: External access token (RFC 6750) for MCP server resource access
    - Internal JWT: Gateway-to-MCP authentication (always included)
    - API Key: Bearer/Basic/Custom API key authentication

    Args:
        oauth_service: OAuth service for OAuth token management
        server: MCP server document
        auth_context: Gateway authentication context (user, client_id, scopes, jwt_token)
        additional_headers: Optional additional headers to merge
        state_metadata: OAuth flow state metadata
        agentcore_auth_service: AgentCore Runtime auth service for JWT/IAM authentication
        redis_client: Redis client for JWT token caching

    Returns:
        Complete headers dict with authentication

    Raises:
        UrlElicitationRequiredException: If user needs to perform out-of-band re-auth process.
        InternalServerException: If UserContextDict.user_id is None, or if there is unexpected exception
          when building OAuth token on behalf of user.
    """
    # Validate user_id is present (auth-server always includes it in JWT)
    if auth_context["user_id"] is None:
        logger.error(f"Missing user_id in auth_context. Available keys: {list(auth_context.keys())}")
        raise InternalServerException("Invalid authentication context: missing user_id")

    # Build base headers (filter out empty values to avoid httpx errors)
    effective_scopes = effective_scopes_from_context(auth_context)
    headers: dict[str, str] = {
        "X-User-Id": auth_context.get("user_id") or "",
        "X-Username": auth_context.get("username") or "",
        "X-Scopes": " ".join(effective_scopes),
    }
    # Remove empty header values (httpx requires non-empty strings)
    headers = {k: v for k, v in headers.items() if v}

    # Merge additional headers if provided
    if additional_headers:
        headers.update(additional_headers)

    # Build complete authentication headers (OAuth, apiKey, custom, AgentCore Runtime)
    try:
        user_id = auth_context["user_id"]  # Already validated above
        auth_headers = await build_complete_headers_for_server(
            oauth_service,
            server,
            user_id,
            state_metadata=state_metadata,
            agentcore_auth_service=agentcore_auth_service,
            redis_client=redis_client,
        )

        # Merge auth headers with case-insensitive override logic
        # Protected headers that won't be overridden by auth headers
        protected_headers = {"x-user-id", "x-username", "x-client-id", "x-scopes", "accept"}

        # Build a case-insensitive map of existing header names to their original keys
        lowercase_header_map = {k.lower(): k for k in headers}

        for auth_key, auth_value in auth_headers.items():
            auth_key_lower = auth_key.lower()
            if auth_key_lower in protected_headers:
                continue

            # Remove any existing header with same name (case-insensitive)
            existing_key = lowercase_header_map.get(auth_key_lower)
            if existing_key is not None:
                headers.pop(existing_key, None)

            # Add/override with the auth header and update the lowercase map
            headers[auth_key] = auth_value
            lowercase_header_map[auth_key_lower] = auth_key

        logger.debug(f"Built complete authentication headers for {server.serverName}")
        return headers

    except OAuthReAuthRequiredError as exc:
        logger.debug(f"in-session re-auth required for server {exc.server_name}")

        raise UrlElicitationRequiredException(
            "OAuth re-authentication required", auth_url=exc.auth_url, server_name=exc.server_name
        )
    except (OAuthTokenError, AuthenticationError):
        logger.exception("unexpected OAuth token exception")

        raise InternalServerException("internal server error when building OAuth token on behalf of user")


def build_target_url(server: ExtendedMCPServer, remaining_path: str = "") -> str:
    """
    Build complete target URL for proxying to MCP server.
    Consolidates URL building logic used across all proxy endpoints.

    Args:
        server: MCP server document
        remaining_path: Optional path to append after server base URL

    Returns:
        Complete target URL

    Raises:
        InternalServerException: If server URL is not configured.
    """
    config = server.config or {}
    base_url = config.get("url")

    if not base_url:
        raise InternalServerException("Server URL not configured")

    # If no remaining path, return base URL as-is
    if not remaining_path:
        return base_url

    # Ensure base URL has trailing slash before appending path
    if not base_url.endswith("/"):
        base_url += "/"

    return base_url + remaining_path


def parse_elicitation_id(auth_url: str) -> str | None:
    """
    Parse the auth_url to obtain the elicitation_id from the "state" query string parameter.
    """

    try:
        parsed = urlsplit(auth_url)

        qs_dict = parse_qs(parsed.query)

        state_str = qs_dict["state"][0]

        state_dict = FlowStateManager.decode_state(state_str)

        elicitation_id = state_dict["meta"]["elicitation_id"]

        if UUID(elicitation_id).version != 4:
            logger.error("elicitation_id from the state dictionary is not a valid UUID4 string.")

            return None

        return elicitation_id
    except Exception:
        logger.exception("failed to extract elicitation_id from auth_url.")

        return None
