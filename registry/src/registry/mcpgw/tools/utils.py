"""
Dynamic MCP server proxy routes.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from bson import ObjectId
from mcp.server.mcpserver import Context
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp.types import (
    CallToolResult,
    ElicitRequest,
    ElicitRequestURLParams,
    InitializeRequestParams,
    InputRequiredResult,
    TextContent,
)
from mcp_types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    LATEST_HANDSHAKE_VERSION,
    MODERN_PROTOCOL_VERSIONS,
)

from registry_pkgs.core.exceptions import InternalServerException
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.oauth.flow_state_manager import FlowStateManager
from registry_pkgs.oauth.types import ClientBranding, StateMetadata

from ...auth.dependencies import UserContextDict
from ...constants import UrlElicitationInputKey
from ..core.types import McpAppContext

logger = logging.getLogger(__name__)

# The MCP revision that introduced URL mode elicitation (SEP-1036), with the `url` capability and -32042.
_URL_ELICITATION_MIN_PROTOCOL_VERSION = "2025-11-25"


def _extract_authenticated_user_context(ctx: Context[McpAppContext]) -> UserContextDict | None:
    try:
        user_context: UserContextDict = ctx.request_context.request.state.user  # type: ignore[union-attr]
    except AttributeError:
        return None

    user_id = user_context.get("user_id") if user_context else None
    if not user_id or not ObjectId.is_valid(user_id):
        return None

    return user_context


def get_client_params(ctx: Context[McpAppContext]) -> InitializeRequestParams | None:
    """Load this request's cached client ``initialize`` params, at most one Redis GET per request.

    The source depends on the protocol era. On a 2026-07-28 request the SDK builds
    ``ctx.session.client_params`` from the request ``_meta`` whenever the client sent ``clientInfo``, so we
    read it there and never touch Redis (a stateless modern request caches nothing). On a 2025-11-25
    stateless request the SDK records no client params; they are cached in Redis by
    ``ClientInfoCaptureMiddleware`` and read back here. The result is memoized on
    ``request.state.mcp_client_params``. Returns ``None`` when the client sent no cacheable client info —
    callers then fall back to a plain-text URL and ``UNRECOGNIZED`` branding.
    """
    request = ctx.request_context.request
    if request is None:
        return None
    try:
        return request.state.mcp_client_params
    except AttributeError:
        pass

    params: InitializeRequestParams | None = None
    if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
        params = ctx.session.client_params
    else:
        user_context = _extract_authenticated_user_context(ctx)
        if user_context is not None:
            key = ctx.request_context.lifespan_context.client_info_store.key_for(user_context)
            if key is not None:
                params = ctx.request_context.lifespan_context.client_info_store.load(key)

    request.state.mcp_client_params = params
    return params


def _negotiated_protocol_version(requested: str | int) -> str:
    """Return the protocol version the session speaks, given the version the client requested.

    Mirrors v2's handshake negotiation: a handshake version is echoed back, anything else (including
    the modern ``2026-07-28`` era and unknown versions) is counter-offered ``LATEST_HANDSHAKE_VERSION``
    (``2025-11-25``). v2's ``LATEST_PROTOCOL_VERSION`` is ``2026-07-28``, which handshake must not return.
    """
    return requested if requested in HANDSHAKE_PROTOCOL_VERSIONS else LATEST_HANDSHAKE_VERSION


def _support_url_elicitation(ctx: Context[McpAppContext]) -> bool:
    """Return whether this request's MCP client supports URL-mode elicitation.

    On 2026-07-28 the client declares capabilities through the request ``_meta`` even when it omits
    ``clientInfo``, so we read ``ctx.client_capabilities`` directly (``get_client_params`` can be ``None``
    for a modern client that sent no ``clientInfo``). On 2025-11-25 URL mode requires both the declared
    ``url`` elicitation capability and a negotiated protocol version that defines it, read from the cached
    ``initialize`` params. Codex (``codex-mcp-client``) declares ``url`` on 2025-06-18 and does not handle
    -32042, so it gets the fallback ``CallToolResult``.

    There are deliberately no per-client overrides keyed on clientInfo.name. Claude Code CLI and Claude
    Desktop's Chat mode both identify as "claude-code" (observed October 2026), but Claude Desktop hangs
    on a -32042 URL elicitation error response, while it handles the fallback `CallToolResult` correctly.
    Claude Desktop's behaviour on a 2026-07-28 ``input_required`` result has not been verified.
    """
    if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
        capabilities = ctx.client_capabilities
        if capabilities is None:
            return False
        elicitation = capabilities.elicitation
        return elicitation is not None and elicitation.url is not None

    client_params = get_client_params(ctx)
    if client_params is None:
        return False

    elicitation = client_params.capabilities.elicitation
    if elicitation is None or elicitation.url is None:
        return False

    # Safe as a string comparison: the negotiated value is always a YYYY-MM-DD revision from the SDK's list.
    return _negotiated_protocol_version(client_params.protocol_version) >= _URL_ELICITATION_MIN_PROTOCOL_VERSION


def _get_state_metadata(client_params: InitializeRequestParams | None) -> StateMetadata:
    """Build consent state metadata carrying the client branding for deep-link-back."""
    if client_params is None:
        return {"client_branding": ClientBranding.UNRECOGNIZED}

    name = client_params.client_info.name.strip().lower()
    if name == "visual studio code":
        branding = ClientBranding.VSCODE
    elif name.startswith("probe (via mcp-remote") or name.startswith("mcp-stdio-client (via mcp-remote"):
        branding = ClientBranding.CURSOR
    else:
        branding = ClientBranding.UNRECOGNIZED

    return {"client_branding": branding}


def authorization_declined_result(ctx: Context[McpAppContext]) -> CallToolResult | None:
    """Stop this invocation when the modern client declined Registry authorization."""
    if ctx.protocol_version not in MODERN_PROTOCOL_VERSIONS or not ctx.input_responses:
        return None
    if not any(
        getattr(ctx.input_responses.get(key), "action", None) in {"decline", "cancel"} for key in UrlElicitationInputKey
    ):
        return None
    return _declined_authorization_result()


def _declined_authorization_result() -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    "The user declined or cancelled the authorization, so the request was not "
                    "performed. Do not retry unless the user asks you to."
                ),
            )
        ],
        is_error=True,
    )


def _build_url_elicitation_result(
    ctx: Context[McpAppContext],
    auth_url: str,
    llm_template: str,
    human_message: str,
    *,
    input_key: UrlElicitationInputKey,
    elicitation_id: str | None = None,
) -> CallToolResult | InputRequiredResult:
    """Build a URL elicitation response or fallback result, keyed on the negotiated protocol era.

    2026-07-28 delivers URL elicitation as an ``InputRequiredResult`` tool result that the client answers
    by retrying the same call with ``input_responses``; 2025-11-25 delivers it as a ``-32042`` error
    (``UrlElicitationRequiredError``). Both eras re-run the consent/token checks on the retry, so the
    client's answer is never trusted and no ``request_state`` is sealed. Unsupported clients get a
    plain-text ``CallToolResult`` fallback.

    ``input_key`` names the failing check. A modern retry suppresses the prompt only when it answers that
    same key, so a chain of different checks (consent, then downstream OAuth) prompts once per check, while
    a check that still fails after its own prompt returns text. The SDK client sends only the latest round's
    answers, so each check prompts at most once in a row and the chain always terminates.
    """
    if elicitation_id is None:
        elicitation_id = parse_elicitation_id(auth_url)
    llm_message = llm_template.format(f"URL `{auth_url}`")
    modern = ctx.protocol_version in MODERN_PROTOCOL_VERSIONS

    # 1. Modern retry of the same check: the client answered this check's InputRequiredResult and the check
    #    failed again. Never prompt again — an auto-accepting client (Claude Code `-p`) would otherwise loop
    #    up to the SDK's round limit, saving a pending-consent nonce each round. An answer under a different
    #    key means an earlier check passed and this is the next one, which gets its own prompt below.
    if modern and ctx.input_responses and input_key in ctx.input_responses:
        action = getattr(ctx.input_responses[input_key], "action", None)
        if action != "accept":
            logger.info("Modern URL elicitation retry answered %r; returning declined text.", action)
            return _declined_authorization_result()
        logger.info("Modern URL elicitation retry accepted but the check still failed; returning URL text.")
        return CallToolResult(content=[TextContent(type="text", text=llm_message)], isError=True)

    supported = _support_url_elicitation(ctx)

    # 2. Modern first round: deliver URL elicitation as an input_required tool result. No request_state is
    #    sealed (the gate re-checks consent and tokens on the retry). elicitation_id stays off the wire —
    #    it is logged here for correlation — so no 2026-07-28 response carries it.
    if supported and modern:
        logger.info("Sending a 2026-07-28 URL mode input_required result (elicitation id %s).", elicitation_id)
        return InputRequiredResult(
            input_requests={
                input_key: ElicitRequest(
                    params=ElicitRequestURLParams(url=auth_url, message=human_message),
                )
            }
        )

    # 3. Legacy capable: the SDK turns this into a -32042 URL mode elicitation error response.
    if supported and elicitation_id is not None:
        logger.info("sending back the URL mode elicitation error response with ID %s.", elicitation_id)
        raise UrlElicitationRequiredError(
            message=llm_message,
            elicitations=[
                ElicitRequestURLParams(
                    elicitationId=elicitation_id,
                    url=auth_url,
                    message=human_message,
                )
            ],
        )

    # 4. Fallback: plain-text CallToolResult for clients that do not support URL elicitation.
    client_params = get_client_params(ctx)
    if client_params is None:
        logger.info(
            "Not using URL mode elicitation (no cached client params, elicitation ID found: %s). "
            "Sending back a tool call result with the URL.",
            elicitation_id is not None,
        )
    else:
        elicitation = client_params.capabilities.elicitation
        logger.info(
            "Not using URL mode elicitation (client %r %r, requested protocol version %r, declared url: %s, "
            "elicitation ID found: %s). Sending back a tool call result with the URL.",
            client_params.client_info.name,
            client_params.client_info.version,
            client_params.protocol_version,
            elicitation is not None and elicitation.url is not None,
            elicitation_id is not None,
        )
    return CallToolResult(
        content=[TextContent(type="text", text=llm_message)],
        isError=True,
    )


def get_progress_token(ctx: Context[McpAppContext]) -> str | int | None:
    """Read only a valid progress token from this request's metadata."""
    meta = getattr(ctx.request_context, "meta", None)
    if not isinstance(meta, dict):
        return None
    raw_params = getattr(ctx.request_context, "params", None)
    if isinstance(raw_params, dict):
        raw_meta = raw_params.get("_meta")
        if isinstance(raw_meta, dict) and type(raw_meta.get("progressToken")) not in (str, int):
            # Pydantic can coerce booleans/floats to integers; the original wire token must also be valid.
            return None
    # The SDK's validated RequestParamsMeta uses snake_case keys, even though the wire uses camelCase.
    token = meta.get("progress_token")
    # bool is an int subclass, but is not a protocol progress token.
    return token if type(token) in (str, int) else None


def get_target_url(server: ExtendedMCPServer) -> str:
    """
    Get target MCP server URL for proxying to MCP server.
    Consolidates URL building logic used across all proxy endpoints.

    Args:
        server: MCP server document

    Returns:
        Target MCP server URL

    Raises:
        InternalServerException: If server URL is not configured.
    """
    config = server.config or {}
    url = config.get("url")

    if url is None:
        logger.error(f"Server URL is not configured for server '{server.serverName}'.")

        raise InternalServerException("Server URL not configured")

    return url


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
