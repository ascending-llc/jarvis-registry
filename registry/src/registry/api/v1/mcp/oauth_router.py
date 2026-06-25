import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from bson import ObjectId
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.session import ServerSession
from redis import Redis

from registry_pkgs.core.downstream_oauth import downstream_mcp_code_key, oauth_error_payload
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token
from registry_pkgs.core.oauth_state_store import DownstreamOAuthStoreProtocol
from registry_pkgs.core.redirect_uri import redirect_uri_matches, validate_registration_redirect_uri

from ....auth.dependencies import CurrentUser
from ....auth.oauth.reconnection import OAuthReconnectionManager
from ....auth.oauth.types import ClientBranding
from ....constants import DownstreamOAuthConstants
from ....core.config import settings
from ....core.mcp_client import get_oauth_metadata_from_server
from ....core.session_store import SessionStore
from ....deps import (
    get_mcp_service,
    get_oauth_state_store,
    get_reconnection_manager,
    get_redis_client,
    get_server_service,
    get_session_store,
    get_token_service,
)
from ....schemas.common_api_schemas import (
    OAuthInitiateResponse,
    OAuthMetadataDiscoverResponse,
    OAuthOperationResponse,
    OAuthTokensResponse,
)
from ....schemas.enums import ConnectionState, OAuthFlowStatus
from ....schemas.oauth_schema import MCPClientContext, OAuthFlow
from ....services.oauth.mcp_service import MCPService
from ....services.oauth.token_service import TokenService
from ....services.server_service import ServerServiceV1
from ....utils.schema_converter import convert_dict_keys_to_camel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["oauth"])


@router.get("/oauth/discover", response_model=OAuthMetadataDiscoverResponse, response_model_by_alias=True)
async def discover_oauth_metadata(
    url: str = Query(..., description="MCP server URL to discover OAuth metadata from"),
) -> OAuthMetadataDiscoverResponse:
    """
    Discover OAuth metadata from MCP server's well-known endpoints.

    This endpoint is used during the pre-server creation process to fetch OAuth metadata
    before registering a server in the system.

    Fetches OAuth server metadata using RFC 8414 and OIDC Discovery standards:
    - /.well-known/oauth-authorization-server (RFC 8414)
    - /.well-known/openid-configuration (OIDC Discovery)

    Args:
        url: The MCP server URL to discover OAuth metadata from

    Returns:
        Always returns 200 OK with:
        - server_url: The requested server URL
        - metadata: OAuth metadata dict (if found) or None (if not found)
        - message: Success or failure message

        When metadata is found, includes:
        - issuer: OAuth issuer URL
        - authorization_endpoint: URL for authorization requests
        - token_endpoint: URL for token exchange
        - response_types_supported: Supported OAuth response types
        - grant_types_supported: Supported OAuth grant types
        - scopes_supported: Supported OAuth scopes (if available)
    """
    try:
        if not url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Server URL is required")

        logger.info(f"[OAuth Discovery] Discovering OAuth metadata for URL {url}")

        # Discover OAuth metadata
        metadata = await get_oauth_metadata_from_server(url)

        if not metadata:
            logger.info(
                f"[OAuth Discovery] No OAuth metadata found for {url} - server may not support OAuth autodiscovery"
            )
            return OAuthMetadataDiscoverResponse(
                serverUrl=url,
                metadata=None,
                message="OAuth metadata could not be discovered. This server may not support OAuth autodiscovery or does not expose well-known endpoints.",
            )

        logger.info(
            f"[OAuth Discovery] Successfully discovered OAuth metadata for {url}: "
            f"authorization_endpoint={metadata.get('authorization_endpoint', 'N/A')}, "
            f"token_endpoint={metadata.get('token_endpoint', 'N/A')}"
        )

        return OAuthMetadataDiscoverResponse(
            serverUrl=url,
            metadata=convert_dict_keys_to_camel(metadata),
            message="OAuth metadata discovered successfully.",
        )

    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"[OAuth Discovery] Failed to discover OAuth metadata: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to discover OAuth metadata: {str(e)}",
        )


@router.get("/{server_id}/oauth/initiate", response_model=OAuthInitiateResponse, response_model_by_alias=True)
async def initiate_oauth_flow(
    server_id: str,
    user_context: CurrentUser,
    mcp_service: MCPService = Depends(get_mcp_service),
    server_service: ServerServiceV1 = Depends(get_server_service),
) -> OAuthInitiateResponse:
    """
    Initialize OAuth flow

    Notes: GET /:serverName/oauth/initiate
    TypeScript implementation: Directly call MCPOAuthHandler.initiateOAuthFlow()
    """
    try:
        user_id = user_context.get("user_id")
        logger.info(f"Oauth initiate for user id : {user_id}")
        server = await server_service.get_server_by_id(server_id)
        if not server:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

        flow_id, auth_url, error = await mcp_service.oauth_service.initiate_oauth_flow(user_id=user_id, server=server)
        if error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

        if not flow_id or not auth_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initiate OAuth flow"
            )

        return OAuthInitiateResponse(
            flowId=flow_id,
            authorizationUrl=auth_url,
            serverId=server_id,
            userId=user_id,
            serverName=server.serverName,
        )
    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"Failed to initialize OAuth flow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to initialize OAuth flow: {str(e)}"
        )


async def _reconnect_after_oauth(
    mcp_service: MCPService,
    reconnection_manager: OAuthReconnectionManager,
    flow: OAuthFlow | None,
    server_path: str,
) -> None:
    """Best-effort: mark the user's connection CONNECTED and clear reconnection attempts.

    Failures here never block the callback redirect — the tokens are already saved.
    """
    if not (flow and flow.user_id):
        return
    user_id, server_id = flow.user_id, flow.server_id
    try:
        await mcp_service.connection_service.create_user_connection(
            user_id=user_id,
            server_id=server_id,
            initial_state=ConnectionState.CONNECTED,
            details={"oauth_completed": True, "flow_id": flow.flow_id, "created_at": time.time()},
        )
        logger.info(f"[MCP OAuth] Reconnected {server_path} (server_id: {server_id}) for user {user_id}")
        try:
            reconnection_manager.clear_reconnection(user_id, server_id)
        except Exception as e:
            logger.error(f"[MCP OAuth] Could not clear reconnection (manager not initialized): {e}")
    except Exception as e:
        logger.error(f"[MCP OAuth] Failed to reconnect {server_path} after OAuth, but tokens are saved: {e}")


async def _notify_elicitation_complete(
    state_dict: dict[str, Any],
    session_store: SessionStore,
) -> ClientBranding | None:
    """Best-effort ``elicitation/complete`` notification for mcpgw-initiated flows.

    Returns the client branding carried in the flow state (used to deep-link the user back to their
    AI app), or None. Never raises.
    """
    meta = state_dict.get("meta")
    if not meta or "elicitation_id" not in meta:
        return None

    client_branding: ClientBranding | None = meta.get("client_branding")
    try:
        elicitation_id = meta["elicitation_id"]
        if not meta["notify_elicitation_complete"]:
            # Client connected via the dynamic catch-all route; no live session to notify.
            logger.info("MCP client connected via dynamic catch-all route. Not sending elicitation/complete.")
            return client_branding

        session: ServerSession | None = session_store.pop(elicitation_id)
        if session is None:
            logger.warning(f"could not find session object for elicitation_id {elicitation_id}")
            return client_branding

        await session.send_elicit_complete(elicitation_id)
        logger.info(f"scheduled elicitation/complete notification for elicitation_id {elicitation_id}")
    except Exception:
        logger.exception("failed to send elicitation/complete notification to client.")
    return client_branding


def _build_downstream_client_redirect(
    flow: OAuthFlow | None,
    redis_client: Redis,
) -> RedirectResponse | None:
    """For an MCP-client-initiated (Layer B) flow, stash the PKCE/binding context under a fresh
    authorization code and build the redirect back to the client's ``redirect_uri``. Returns None
    for registry-frontend flows.

    The access/refresh tokens are NOT minted here — they are issued at ``/token`` exchange time once
    the client redeems this code with its PKCE verifier.
    """
    ctx = flow.metadata.mcp_client_context if (flow and flow.metadata) else None
    if ctx is None or not (flow and flow.user_id):
        return None

    b_code = secrets.token_urlsafe(32)

    _ = redis_client.setex(
        downstream_mcp_code_key(b_code),
        DownstreamOAuthConstants.CODE_TTL_SECONDS,
        json.dumps(
            {
                "code_challenge": ctx["code_challenge"],
                "client_id": ctx["client_id"],
                "redirect_uri": ctx["redirect_uri"],
                "user_id": flow.user_id,
                "server_path": ctx["server_path"],
            }
        ),
    )
    return RedirectResponse(url=_append_query_params(ctx["redirect_uri"], code=b_code, state=ctx["state"]))


@router.get("/{server_path}/oauth/callback")
async def oauth_callback(
    server_path: str,
    request: Request,
    code: str | None = Query(None, description="OAuth authorization code"),
    state: str | None = Query(None, description="State parameter (format: flow_id##security_token)"),
    error: str | None = Query(None, description="OAuth error message"),
    mcp_service: MCPService = Depends(get_mcp_service),
    reconnection_manager: OAuthReconnectionManager = Depends(get_reconnection_manager),
    session_store: SessionStore = Depends(get_session_store),
    redis_client: Redis = Depends(get_redis_client),
) -> RedirectResponse:
    """
    OAuth callback handler

    Notes: /:serverName/oauth/callback

    Process:
    1. Check for errors returned by OAuth provider
    2. Validate required parameters (code, state)
    3. Decode state to get flow_id and security_token
    4. Complete OAuth flow (validation + token exchange)
    5. Redirect to success/failure page
    """
    try:
        # 1. Provider returned an error, or required params are missing.
        if error:
            logger.error(f"[MCP OAuth] OAuth error received from provider: {error}")
            return _redirect_to_page(request, server_path, error_msg=error)
        if not code or not isinstance(code, str):
            logger.error("[MCP OAuth] Missing or invalid authorization code")
            return _redirect_to_page(request, server_path, error_msg="missing_code")
        if not state or not isinstance(state, str):
            logger.error("[MCP OAuth] Missing or invalid state parameter")
            return _redirect_to_page(request, server_path, error_msg="missing_state")

        # 2. Decode flow_id from state.
        try:
            state_dict = mcp_service.oauth_service.flow_manager.decode_state(state)
            flow_id = state_dict["flow_id"]
        except ValueError as e:
            logger.error(f"[MCP OAuth] Failed to decode state: {e}")
            return _redirect_to_page(request, server_path, error_msg="invalid_state_format")
        logger.info(f"[MCP OAuth] Callback received: server={server_path}, flow_id={flow_id}")

        # 3. Short-circuit a duplicate callback for an already-completed flow.
        flow = mcp_service.oauth_service.flow_manager.get_flow(flow_id)
        if flow and flow.status == OAuthFlowStatus.COMPLETED:
            logger.warning(f"[MCP OAuth] Flow already completed, preventing duplicate token exchange: {flow_id}")
            return _redirect_to_page(request, server_path, flag="success")

        # 4. Complete the flow (validate state + exchange tokens for downstream MCP tokens).
        success, error_msg = await mcp_service.oauth_service.complete_oauth_flow(
            flow_id=flow_id, authorization_code=code, state=state
        )
        if not success:
            logger.error(f"[MCP OAuth] Failed to complete OAuth flow: {error_msg}")
            return _redirect_to_page(request, server_path, error_msg=error_msg or "unknown_error")
        logger.info(f"[MCP OAuth] OAuth flow completed successfully for {server_path}")

        flow = mcp_service.oauth_service.flow_manager.get_flow(flow_id)

        # 5. Best-effort post-completion side effects (never block the redirect).
        await _reconnect_after_oauth(mcp_service, reconnection_manager, flow, server_path)
        client_branding = await _notify_elicitation_complete(state_dict, session_store)

        # 6. MCP-client-initiated flows redirect back to the client;
        downstream_redirect = _build_downstream_client_redirect(flow, redis_client)
        if downstream_redirect is not None:
            logger.info(f"[MCP OAuth] Downstream MCP-client flow complete for {server_path}; redirecting to client")
            return downstream_redirect

        return _redirect_to_page(request, server_path, flag="success", client_branding=client_branding)

    except Exception as e:
        logger.error(f"[MCP OAuth] OAuth callback error: {str(e)}", exc_info=True)
        return _redirect_to_page(request, server_path, error_msg="callback_failed")


@router.get("/oauth/tokens/{flow_id}", response_model=OAuthTokensResponse, response_model_by_alias=True)
async def get_oauth_tokens(
    flow_id: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> OAuthTokensResponse:
    """
    Get OAuth tokens

    Notes: GET /oauth/tokens/:flowId
    TypeScript implementation: Get tokens via flowManager.getFlowState()

    Parameters:
    - flow_id: Flow ID
    - current_user: Current user information

    Returns:
    - OAuth tokens
    """
    try:
        user_id = current_user.get("user_id")

        # 1. Verify flow_id belongs to current user
        if not flow_id.startswith(f"{user_id}") and not flow_id.startswith("system:"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to access this flow")

        # 2. Get tokens by flow ID
        tokens = await mcp_service.oauth_service.get_tokens_by_flow_id(flow_id)
        if not tokens:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tokens not found or flow not completed")

        # 3. Return tokens
        return OAuthTokensResponse(tokens=tokens.model_dump() if hasattr(tokens, "model_dump") else tokens)
    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"Failed to get OAuth tokens: {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get tokens: {str(e)}")


@router.get("/oauth/status/{flow_id}")
async def get_oauth_status(flow_id: str, mcp_service: MCPService = Depends(get_mcp_service)) -> dict[str, Any]:
    """
    Check OAuth flow status

    Notes: GET /oauth/status/:flowId
    TypeScript implementation: Get status via flowManager.getFlowState()

    """
    try:
        # Get flow status
        flow_status = await mcp_service.oauth_service.get_flow_status(flow_id)

        return flow_status

    except Exception as e:
        logger.error(f"Failed to check OAuth flow status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to check flow status: {str(e)}"
        )


@router.post("/oauth/cancel/{server_id}", response_model=OAuthOperationResponse, response_model_by_alias=True)
async def cancel_oauth_flow(
    server_id: str,
    current_user: CurrentUser,
    mcp_service: MCPService = Depends(get_mcp_service),
    server_service: ServerServiceV1 = Depends(get_server_service),
) -> OAuthOperationResponse:
    """
    Cancel OAuth flow

    Notes: POST /oauth/cancel/:serverName
    TypeScript implementation: Directly call flowManager.failFlow()

    Process:
    1. Cancel the OAuth flow
    2. Update user connection state to DISCONNECTED
    """
    try:
        user_id = str(current_user.get("user_id"))
        logger.info(f"[OAuth Cancel] Cancelling OAuth flow for {server_id} by user {user_id}")

        mcp_server = await server_service.get_server_by_id(server_id)
        if not mcp_server:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

        # 1. Cancel the OAuth flow
        success, error_msg = await mcp_service.oauth_service.cancel_oauth_flow(user_id, server_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg or "Failed to cancel OAuth flow"
            )

        # 2. Update user connection state to DISCONNECTED
        try:
            await mcp_service.connection_service.update_connection_state(
                user_id=user_id,
                server_id=server_id,
                state=ConnectionState.DISCONNECTED,
                details={"oauth_cancelled": True, "cancelled_at": time.time(), "reason": "User cancelled OAuth flow"},
            )
            logger.info(f"[OAuth Cancel] Updated connection state to DISCONNECTED for {server_id}")
        except Exception as e:
            logger.warning(f"[OAuth Cancel] Failed to update connection state: {e}")

        return OAuthOperationResponse(
            success=True,
            message=f"OAuth flow for {mcp_server.serverName} cancelled successfully",
            serverId=server_id,
            userId=user_id,
            serverName=mcp_server.serverName,
        )
    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"Failed to cancel OAuth flow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to cancel flow: {str(e)}"
        )


@router.post("/oauth/refresh/{server_id}", response_model=OAuthOperationResponse, response_model_by_alias=True)
async def refresh_oauth_tokens(
    server_id: str,
    current_user: CurrentUser,
    mcp_service: MCPService = Depends(get_mcp_service),
    server_service: ServerServiceV1 = Depends(get_server_service),
    reconnection_manager: OAuthReconnectionManager = Depends(get_reconnection_manager),
) -> OAuthOperationResponse:
    """
    Refresh OAuth tokens

    Notes: POST /oauth/refresh/:serverName
    TypeScript implementation: Call MCPOAuthHandler.refreshOAuthTokens()

    Process:
    1. Refresh OAuth tokens
    2. Update user connection state to CONNECTED
    3. Clear any reconnection attempts
    """
    try:
        user_id = str(current_user.get("user_id"))
        logger.info(f"[OAuth Refresh] Refreshing OAuth tokens for {server_id} by user {user_id}")
        mcp_server = await server_service.get_server_by_id(server_id)
        if not mcp_server:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

        # 1. Refresh OAuth tokens
        success, error_msg = await mcp_service.oauth_service.validate_and_refresh_tokens(user_id, mcp_server)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg or "Failed to refresh tokens")

        logger.info(f"[OAuth Refresh] Successfully refreshed tokens for {server_id}")

        # 2. Update user connection state to CONNECTED
        try:
            # Check if user connection exists, if not create it
            connection = await mcp_service.connection_service.get_connection(user_id, server_id)
            if connection:
                # Update existing connection
                await mcp_service.connection_service.update_connection_state(
                    user_id=user_id,
                    server_id=server_id,
                    state=ConnectionState.CONNECTED,
                    details={"oauth_refreshed": True, "refreshed_at": time.time()},
                )
                logger.info(f"[OAuth Refresh] Updated connection state to CONNECTED for {server_id}")
            else:
                # Create new connection if it doesn't exist
                await mcp_service.connection_service.create_user_connection(
                    user_id=user_id,
                    server_id=server_id,
                    initial_state=ConnectionState.CONNECTED,
                    details={"oauth_refreshed": True, "created_at": time.time(), "refreshed_at": time.time()},
                )
                logger.info(f"[OAuth Refresh] Created new connection with CONNECTED state for {server_id}")
        except Exception as e:
            logger.warning(f"[OAuth Refresh] Failed to update connection state: {e}")

        # 3. Clear any reconnection attempts
        try:
            reconnection_manager.clear_reconnection(user_id, server_id)
            logger.debug(f"[OAuth Refresh] Cleared reconnection attempts for {server_id}")
        except Exception as e:
            logger.warning(f"[OAuth Refresh] Could not clear reconnection (manager not initialized): {e}")

        return OAuthOperationResponse(
            success=True,
            message=f"Tokens refreshed successfully for {mcp_server.serverName}",
            serverId=server_id,
            userId=user_id,
            serverName=mcp_server.serverName,
        )
    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"Failed to refresh OAuth tokens: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to refresh tokens: {str(e)}"
        )


@router.delete("/oauth/token/{server_id}", response_model=OAuthOperationResponse, response_model_by_alias=True)
async def delete_oauth_tokens(
    server_id: str,
    current_user: CurrentUser,
    mcp_service: MCPService = Depends(get_mcp_service),
    server_service: ServerServiceV1 = Depends(get_server_service),
    token_service: TokenService = Depends(get_token_service),
) -> OAuthOperationResponse:
    """
    Delete the OAuth token for this user

    """
    server = await server_service.get_server_by_id(server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    user_id = str(current_user.get("user_id"))
    try:
        disconnected = await mcp_service.connection_service.disconnect_user_connection(user_id, server_id)
        if disconnected:
            logger.info(f"[Delete OAuth Tokens] Disconnected {server_id} for user {user_id}")
        results = await token_service.delete_oauth_tokens(user_id, server.serverName)
        logger.info(f"[Delete OAuth Tokens] Deleted OAuth tokens for {server_id}, results: {results}")
        message = "successfully" if results else "failed"
        return OAuthOperationResponse(
            success=results,
            message=f"oauth delete {message} for {server_id}",
            serverId=server_id,
            userId=user_id,
        )
    except HTTPException as e:
        logger.error(f"Failed to delete OAuth tokens: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete tokens: {str(e)}"
        )


def _append_query_params(url: str, **params: str) -> str:
    """Append query params to a URL, preserving any query string it already carries."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return str(urlunsplit(parts._replace(query=urlencode(query))))


def _validate_downstream_authorize_params(
    response_type: str,
    code_challenge_method: str,
    redirect_uri: str,
) -> None:
    """Reject unsupported OAuth params before the captured ``redirect_uri`` becomes a redirect sink.

    The registry only ever drives a ``code`` + S256 PKCE flow and later 302-redirects the browser to
    ``redirect_uri`` with the authorization code attached, so reject non-http schemes
    (``javascript:``, ``data:``) and malformed values before they reach that sink. Host allowlisting
    is enforced at authorize time via ``_validate_registered_redirect_uri``.
    """
    if response_type != DownstreamOAuthConstants.SUPPORTED_RESPONSE_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported response_type: {response_type}",
        )

    if code_challenge_method != DownstreamOAuthConstants.SUPPORTED_CODE_CHALLENGE_METHOD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported code_challenge_method: {code_challenge_method}",
        )

    try:
        validate_registration_redirect_uri(redirect_uri)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


def _validate_registered_redirect_uri(client_metadata: dict[str, Any] | None, redirect_uri: str) -> None:
    """Reject an authorize request whose redirect_uri is not registered for this client.

    Per RFC 6749 §4.1.2.1 we MUST NOT redirect to an unverified redirect_uri, so all failures here
    raise an in-place 400 instead of a 302. Unknown clients are rejected (clients must DCR against
    auth-server first). Loopback redirect_uris match scheme+host+path and ignore the port.
    """
    if client_metadata is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown client_id")

    registered = client_metadata.get("redirect_uris") or []
    if not any(redirect_uri_matches(redirect_uri, candidate) for candidate in registered):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not registered for this client",
        )


async def _build_downstream_authorize_redirect(
    *,
    user_id: str,
    server_path: str,
    user_context: CurrentUser,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    state: str,
    mcp_service: MCPService,
    server_service: ServerServiceV1,
    store: DownstreamOAuthStoreProtocol,
) -> RedirectResponse:
    """Validate and initiate the per-server downstream authorization flow."""
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid user_id: {user_id}")

    if user_context["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_id does not match the authenticated session",
        )

    _validate_downstream_authorize_params(response_type, code_challenge_method, redirect_uri)

    registered_path = await server_service.extract_server_path(f"/{server_path}")
    server = await server_service.get_server_by_path(registered_path) if registered_path else None
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Server not found for path '{server_path}'")

    _validate_registered_redirect_uri(store.get_client(client_id), redirect_uri)

    ctx: MCPClientContext = {
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_challenge": code_challenge,
        "state": state,
        "server_path": server_path,
    }
    flow_id, auth_url, error = await mcp_service.oauth_service.initiate_oauth_flow(
        user_id=user_id,
        server=server,
        mcp_client_context=ctx,
    )
    if error or not auth_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error or "Failed to initiate downstream OAuth flow",
        )

    logger.info(f"[Downstream OAuth] authorize: user={user_id} server={server_path} flow={flow_id}")
    return RedirectResponse(url=auth_url)


@router.get("/downstream/oauth/authorize/{user_id}/{server_path:path}")
async def downstream_oauth_authorize(
    user_id: str,
    server_path: str,
    user_context: CurrentUser,
    response_type: str = Query("code"),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    state: str = Query(""),
    mcp_service: MCPService = Depends(get_mcp_service),
    server_service: ServerServiceV1 = Depends(get_server_service),
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
) -> RedirectResponse:
    """Per-server downstream OAuth authorization endpoint (Layer B: registry-as-AS).

    Captures the client's PKCE/redirect context, kicks off the Layer A flow against the upstream
    provider, and 302-redirects the browser there.
    """
    try:
        return await _build_downstream_authorize_redirect(
            user_id=user_id,
            server_path=server_path,
            user_context=user_context,
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
            mcp_service=mcp_service,
            server_service=server_service,
            store=store,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Downstream OAuth] authorize failed: user={user_id} server={server_path}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


def _oauth_token_error(error: str, description: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    """Build an RFC 6749 §5.2 token-endpoint error response.

    OAuth clients (e.g. VS Code, Claude) branch on the machine-readable ``error`` code — notably
    ``invalid_grant`` tells them to drop a stale refresh token and re-authorize — so token-endpoint
    failures MUST use this shape rather than the generic ``{"detail": ...}``.
    """
    return JSONResponse(status_code=status_code, content=oauth_error_payload(error, description))


def _downstream_refresh_data(client_id: str, user_id: str, server_path: str) -> dict[str, str]:
    """Build the refresh-token payload persisted for a direct-connect ``(user_id, server_path)``.

    Both the ``authorization_code`` grant (initial issuance) and the ``refresh_token`` grant
    (rotation) persist the same shape; keep it in one place so the bound fields cannot drift apart.
    """
    return {
        "client_id": client_id,
        "user_id": user_id,
        "server_path": server_path,
        "scope": DownstreamOAuthConstants.PROXY_OPS_SCOPE,
    }


def _mint_downstream_access_token(user_id: str, client_id: str, server_path: str) -> str:
    """Mint a managed-agent access token scoped to one direct-connect ``(user_id, server_path)``."""
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject=user_id,
        client_id=client_id,
        expires_in_seconds=DownstreamOAuthConstants.ACCESS_TOKEN_TTL_SECONDS,
        iat=int(time.time()),
        extra_claims={
            "user_id": user_id,
            "server_path": server_path,
            "scope": DownstreamOAuthConstants.PROXY_OPS_SCOPE,
        },
    )


def _downstream_token_response(access_token: str, refresh_token: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": DownstreamOAuthConstants.ACCESS_TOKEN_TTL_SECONDS,
            "refresh_token": refresh_token,
            "scope": DownstreamOAuthConstants.PROXY_OPS_SCOPE,
        },
    )


def _downstream_authorization_code_grant(
    *,
    store: DownstreamOAuthStoreProtocol,
    redis_client: Redis,
    user_id: str,
    server_path: str,
    code: str | None,
    client_id: str,
    client_secret: str | None,
    code_verifier: str | None,
    redirect_uri: str | None,
) -> JSONResponse:
    """Exchange a Layer B authorization code + PKCE verifier for an access + refresh token pair."""
    if not code or not code_verifier or not redirect_uri:
        return _oauth_token_error("invalid_request", "code, code_verifier and redirect_uri are required")

    if not store.validate_client_credentials(client_id, client_secret):
        return _oauth_token_error("invalid_client", "invalid client credentials")

    raw = redis_client.getdel(downstream_mcp_code_key(code))
    if not raw:
        return _oauth_token_error("invalid_grant", "invalid or expired code")

    try:
        entry = json.loads(raw)
        stored_challenge = entry["code_challenge"]
        stored_client_id = entry["client_id"]
        stored_redirect_uri = entry["redirect_uri"]
        bound_user_id = entry["user_id"]
        bound_server_path = entry["server_path"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"[Downstream OAuth] corrupt code entry for code exchange: {e}")
        return _oauth_token_error("invalid_grant", "invalid or expired code")

    if create_s256_code_challenge(code_verifier) != stored_challenge:
        return _oauth_token_error("invalid_grant", "PKCE verification failed")

    if client_id != stored_client_id:
        return _oauth_token_error("invalid_client", "client_id mismatch")

    if redirect_uri != stored_redirect_uri:
        return _oauth_token_error("invalid_grant", "redirect_uri mismatch")

    # Bind the code to the (user_id, server_path) it was issued for, so a leaked code cannot be
    # redeemed under a different token endpoint URL.
    if user_id != bound_user_id or server_path != bound_server_path:
        return _oauth_token_error("invalid_grant", "code does not match this endpoint")

    access_token = _mint_downstream_access_token(bound_user_id, client_id, bound_server_path)
    refresh_token = secrets.token_urlsafe(32)
    store.save_refresh_token(
        refresh_token,
        _downstream_refresh_data(client_id, bound_user_id, bound_server_path),
    )

    logger.info(f"[Downstream OAuth] token issued: user={user_id} server={server_path}")
    return _downstream_token_response(access_token, refresh_token)


def _downstream_refresh_token_grant(
    *,
    store: DownstreamOAuthStoreProtocol,
    user_id: str,
    server_path: str,
    client_id: str,
    client_secret: str | None,
    refresh_token: str | None,
) -> JSONResponse:
    """Rotate a direct-connect refresh token and mint a fresh access token.

    The refresh token is bound to its issuing ``(user_id, server_path)`` so it cannot be redeemed
    under another endpoint URL. Rotation is atomic: a replayed token loses the race and is rejected.
    """
    if not refresh_token:
        return _oauth_token_error("invalid_request", "refresh_token is required")

    if not store.validate_client_credentials(client_id, client_secret):
        return _oauth_token_error("invalid_client", "invalid client credentials")

    token_data = store.get_refresh_token(refresh_token)
    if token_data is None:
        return _oauth_token_error("invalid_grant", "invalid or expired refresh_token")

    if token_data.get("client_id") != client_id:
        return _oauth_token_error("invalid_client", "client_id mismatch")

    if token_data.get("user_id") != user_id or token_data.get("server_path") != server_path:
        return _oauth_token_error("invalid_grant", "refresh_token does not match this endpoint")

    new_refresh_token = secrets.token_urlsafe(32)
    rotated = store.rotate_refresh_token(
        old_token=refresh_token,
        new_token=new_refresh_token,
        new_data=_downstream_refresh_data(client_id, user_id, server_path),
    )
    if rotated is None:
        return _oauth_token_error("invalid_grant", "refresh token already used")

    access_token = _mint_downstream_access_token(user_id, client_id, server_path)
    logger.info(f"[Downstream OAuth] refresh rotated: user={user_id} server={server_path}")
    return _downstream_token_response(access_token, new_refresh_token)


@router.post("/downstream/oauth/token/{user_id}/{server_path:path}")
async def downstream_oauth_token(
    user_id: str,
    server_path: str,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str | None = Form(None),
    code: str | None = Form(None),
    code_verifier: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    refresh_token: str | None = Form(None),
    redis_client: Redis = Depends(get_redis_client),
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
) -> JSONResponse:
    """Per-server downstream OAuth token endpoint (Layer B: registry-as-AS).

    Supports ``authorization_code`` (exchange the Layer B code + PKCE verifier) and ``refresh_token``
    (rotate a stored refresh token). Both mint a managed-agent access token scoped to this
    ``(user_id, server_path)`` direct-connect proxy. No registry Bearer token is required.
    """
    try:
        if grant_type == "authorization_code":
            return _downstream_authorization_code_grant(
                store=store,
                redis_client=redis_client,
                user_id=user_id,
                server_path=server_path,
                code=code,
                client_id=client_id,
                client_secret=client_secret,
                code_verifier=code_verifier,
                redirect_uri=redirect_uri,
            )

        if grant_type == "refresh_token":
            return _downstream_refresh_token_grant(
                store=store,
                user_id=user_id,
                server_path=server_path,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )

        return _oauth_token_error("unsupported_grant_type", f"grant_type '{grant_type}' is not supported")
    except Exception:
        # A dependency failure (e.g. Redis) must still return the RFC 6749 §5.2 error shape, not a
        # bare FastAPI 500 — OAuth clients branch on the JSON ``error`` code, not on ``{"detail"}``.
        logger.exception(f"[Downstream OAuth] token endpoint failed: user={user_id} server={server_path}")
        return _oauth_token_error("server_error", "internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==================== Helper Functions ====================


def _redirect_to_page(
    request: Request,
    server_path: str,
    flag: str = "error",
    error_msg: str | None = None,
    *,
    client_branding: ClientBranding | None = None,
) -> RedirectResponse:
    """
    Generate a response that redirects to frontend OAuth callback page.
        /oauth-callback?type=success&serverPath=value
        /oauth-callback?type=error&serverPath=value&error=value
    """
    host = settings.registry_client_url
    encoded_path = quote(str(server_path))

    # Build full URL with host if request is provided
    redirect_url = f"{host}/oauth-callback?type={flag}&serverPath={encoded_path}"

    if error_msg and flag == "error":
        encoded_error = quote(str(error_msg))
        redirect_url += f"&error={encoded_error}"

    if client_branding is not None:
        redirect_url += f"&clientBranding={quote(client_branding)}"

    logger.info(f"[OAuth Redirect] Redirecting to {flag} page: {redirect_url}")
    return RedirectResponse(url=redirect_url)
