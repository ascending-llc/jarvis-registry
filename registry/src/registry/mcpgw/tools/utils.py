"""
Dynamic MCP server proxy routes.
"""

import logging
from collections import deque
from enum import StrEnum
from typing import Any, Literal

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

from registry_pkgs.models.extended_mcp_server import MCPServerDocument

from ...auth.dependencies import UserContextDict, effective_scopes_from_context
from ...auth.oauth.types import StateMetadata
from ...schemas.errors import AuthenticationError, OAuthReAuthRequiredError, OAuthTokenError
from ...services.server_service import build_complete_headers_for_server
from ..exceptions import InternalServerException, UrlElicitationRequiredException

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
    server: MCPServerDocument,
    auth_context: UserContextDict,
    additional_headers: dict[str, str] | None = None,
    *,
    state_metadata: StateMetadata | None = None,
) -> dict[str, str]:
    """
    Build complete headers with authentication for MCP server requests.
    Consolidates auth logic used by all proxy endpoints.

    Supports dual authentication:
    - setting.auth_egress_header: OAuth/external access token (RFC 6750) for MCP server resource access
    - setting.internal_auth_header: Internal JWT for gateway-to-MCP authentication (always included)

    Args:
        server: MCP server document
        auth_context: Gateway authentication context (user, client_id, scopes, jwt_token)
        additional_headers: Optional additional headers to merge

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

    # Build complete authentication headers (OAuth, apiKey, custom)
    try:
        user_id = auth_context["user_id"]  # Already validated above
        auth_headers = await build_complete_headers_for_server(server, user_id, state_metadata=state_metadata)

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


def build_target_url(server: MCPServerDocument, remaining_path: str = "") -> str:
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


class SessionStore:
    """
    Global singleton object that implements the elicitation_id to ServerSession mapping.
    Before a tool call handler function returns a URL mode elicitation, it sets the map from elicitation_id to session.
    When the /oauth/callback route receives the callback request, on success, it retrieves the session object
    via elicitation_id (passed via the "state" parameter) and uses the session to make a best-effort notification
    to client on elicitation completion.
    """

    _max_session_count: int
    _mapping: dict[str, ServerSession]
    _elicitation_order: deque[str]

    def __init__(self, max_session_count: int = 100):
        self._max_session_count = max_session_count
        self._mapping = {}
        self._elicitation_order = deque(maxlen=self._max_session_count)

    def append(self, elicitation_id: str, session: ServerSession):
        # The elicitation_id to session mapping cannot be updated once set.
        # In practice, elicitation_id is a newly generated UUID for each unique elicitation request,
        # so normally we will not see the same ID being appended again.
        if elicitation_id in self._mapping:
            return

        # If we are at max capacity, pop the oldest elicitation_id and its corresponding session.
        if len(self._mapping) >= self._max_session_count:
            oldest_id = self._elicitation_order.popleft()
            self._mapping.pop(oldest_id, None)

        self._mapping[elicitation_id] = session
        self._elicitation_order.append(elicitation_id)

    def pop(self, elicitation_id: str) -> ServerSession | None:
        try:
            self._elicitation_order.remove(elicitation_id)
        except Exception:
            logger.exception(f"trying to remove elicitation_id {elicitation_id} that doesn't exist in the deque.")

        return self._mapping.pop(elicitation_id, None)


session_store = SessionStore()
