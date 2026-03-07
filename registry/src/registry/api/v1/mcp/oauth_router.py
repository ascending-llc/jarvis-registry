import logging
import time
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from ....auth.dependencies import CurrentUser
from ....auth.oauth.reconnection import get_reconnection_manager
from ....auth.oauth.types import ClientBranding
from ....constants import REGISTRY_CONSTANTS
from ....core.mcp_client import get_oauth_metadata_from_server
from ....mcpgw.tools.utils import session_store
from ....schemas.enums import ConnectionState, OAuthFlowStatus
from ....services.oauth.mcp_service import MCPService, get_mcp_service
from ....services.oauth.token_service import token_service
from ....services.server_service import server_service_v1

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["oauth"])


@router.get("/oauth/discover")
async def discover_oauth_metadata(
    url: str = Query(..., description="MCP server URL to discover OAuth metadata from"),
) -> dict[str, Any]:
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
            return {
                "server_url": url,
                "metadata": None,
                "message": "OAuth metadata could not be discovered. This server may not support OAuth autodiscovery or does not expose well-known endpoints.",
            }

        logger.info(
            f"[OAuth Discovery] Successfully discovered OAuth metadata for {url}: "
            f"authorization_endpoint={metadata.get('authorization_endpoint', 'N/A')}, "
            f"token_endpoint={metadata.get('token_endpoint', 'N/A')}"
        )

        return {
            "server_url": url,
            "metadata": metadata,
            "message": "OAuth metadata discovered successfully.",
        }

    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"[OAuth Discovery] Failed to discover OAuth metadata: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to discover OAuth metadata: {str(e)}",
        )


@router.get("/{server_id}/oauth/initiate")
async def initiate_oauth_flow(
    server_id: str, user_context: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> JSONResponse:
    """
    Initialize OAuth flow

    Notes: GET /:serverName/oauth/initiate
    TypeScript implementation: Directly call MCPOAuthHandler.initiateOAuthFlow()
    """
    try:
        user_id = user_context.get("user_id")
        logger.info(f"Oauth initiate for user id : {user_id}")
        server = await server_service_v1.get_server_by_id(server_id)
        if not server:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

        flow_id, auth_url, error = await mcp_service.oauth_service.initiate_oauth_flow(user_id=user_id, server=server)
        if error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

        if not flow_id or not auth_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initiate OAuth flow"
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "flow_id": flow_id,
                "authorization_url": auth_url,
                "server_id": server_id,
                "user_id": user_id,
                "server_name": server.serverName,
            },
        )
    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"Failed to initialize OAuth flow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to initialize OAuth flow: {str(e)}"
        )


@router.get("/{server_path}/oauth/callback")
async def oauth_callback(
    server_path: str,
    request: Request,
    code: str | None = Query(None, description="OAuth authorization code"),
    state: str | None = Query(None, description="State parameter (format: flow_id##security_token)"),
    error: str | None = Query(None, description="OAuth error message"),
    mcp_service: MCPService = Depends(get_mcp_service),
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
        # 1. Check for errors returned by OAuth provider
        if error:
            logger.error(f"[MCP OAuth] OAuth error received from provider: {error}")
            return _redirect_to_page(request, server_path, error_msg=error)

        # 2. Validate required parameters
        if not code or not isinstance(code, str):
            logger.error("[MCP OAuth] Missing or invalid authorization code")
            return _redirect_to_page(request, server_path, error_msg="missing_code")

        if not state or not isinstance(state, str):
            logger.error("[MCP OAuth] Missing or invalid state parameter")
            return _redirect_to_page(request, server_path, error_msg="missing_state")

        # 3. Decode flow_id from state
        try:
            state_dict = mcp_service.oauth_service.flow_manager.decode_state(state)
            flow_id, security_token = state_dict["flow_id"], state_dict["security_token"]
            logger.info(
                f"[MCP OAuth] Callback received: server={server_path}, "
                f"flow_id={flow_id}, code={'present' if code else 'missing'}, "
                f"security_token_length={len(security_token)}"
            )
        except ValueError as e:
            logger.error(f"[MCP OAuth] Failed to decode state: {e}")
            return _redirect_to_page(request, server_path, error_msg="invalid_state_format")

        # Check if flow is already completed
        flow = mcp_service.oauth_service.flow_manager.get_flow(flow_id)
        if flow and flow.status == OAuthFlowStatus.COMPLETED:
            logger.warning(f"[MCP OAuth] Flow already completed, preventing duplicate token exchange: {flow_id}")
            return _redirect_to_page(request, server_path, flag="success")

        # 4. Complete OAuth flow (validate state + exchange tokens)
        logger.debug(f"[MCP OAuth] Completing OAuth flow for {server_path}")
        success, error_msg = await mcp_service.oauth_service.complete_oauth_flow(
            flow_id=flow_id, authorization_code=code, state=state
        )

        if not success:
            logger.error(f"[MCP OAuth] Failed to complete OAuth flow: {error_msg}")
            return _redirect_to_page(request, server_path, error_msg=error_msg or "unknown_error")

        logger.info(f"[MCP OAuth] OAuth flow completed successfully for {server_path}")

        # 5. Create user connection and setup server
        try:
            # Get user_id and server_id from flow
            flow = mcp_service.oauth_service.flow_manager.get_flow(flow_id)
            if flow and flow.user_id:
                user_id = flow.user_id
                server_id = flow.server_id  # Extract server_id from flow
                logger.debug(
                    f"[MCP OAuth] Attempting to reconnect {server_path} (server_id: {server_id}) with new OAuth tokens"
                )

                # Create user connection with CONNECTED state
                await mcp_service.connection_service.create_user_connection(
                    user_id=user_id,
                    server_id=server_id,  # Use server_id instead of server_name
                    initial_state=ConnectionState.CONNECTED,
                    details={"oauth_completed": True, "flow_id": flow_id, "created_at": time.time()},
                )
                logger.info(
                    f"[MCP OAuth] Successfully reconnected {server_path} (server_id: {server_id}) for user {user_id}"
                )

                # Clear any reconnection attempts
                try:
                    reconnection_manager = get_reconnection_manager(
                        mcp_service=mcp_service, oauth_service=mcp_service.oauth_service
                    )
                    reconnection_manager.clear_reconnection(user_id, server_id)  # Use server_id instead of server_name
                    logger.debug(
                        f"[MCP OAuth] Cleared reconnection attempts for {server_path} (server_id: {server_id})"
                    )
                except Exception as e:
                    logger.error(f"[MCP OAuth] Could not clear reconnection (manager not initialized): {e}")

                logger.debug(f"[MCP OAuth] User connection created for {server_path}")

        except Exception as error:
            logger.error(f"[MCP OAuth] Failed to reconnect {server_path} after OAuth, but tokens are saved: {error}")

        # 6. If the flow started from an URL mode elicitation by mcpgw, make a best-effort notification to client.
        client_branding: ClientBranding | None = None
        try:
            if "meta" in state_dict and "elicitation_id" in state_dict["meta"]:
                client_branding = state_dict["meta"].get("client_branding", None)

                elicitation_id = state_dict["meta"]["elicitation_id"]

                session = session_store.pop(elicitation_id)

                if session is not None:
                    await session.send_elicit_complete(elicitation_id)

                    logger.info(
                        f"successfully scheduled sending elicitation/complete notification for elicitation_id {elicitation_id}"
                    )
                else:
                    logger.info(f"could not find session object for elicitation_id {elicitation_id}")
        except Exception:
            logger.exception("failed to send elicitation/complete notification to client.")

        # 7. Redirect to success page
        return _redirect_to_page(request, server_path, flag="success", client_branding=client_branding)

    except Exception as e:
        logger.error(f"[MCP OAuth] OAuth callback error: {str(e)}", exc_info=True)
        return _redirect_to_page(request, server_path, error_msg="callback_failed")


@router.get("/oauth/tokens/{flow_id}")
async def get_oauth_tokens(
    flow_id: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> dict[str, Any]:
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
        return {"tokens": tokens.dict() if hasattr(tokens, "dict") else tokens}
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


@router.post("/oauth/cancel/{server_id}")
async def cancel_oauth_flow(
    server_id: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> dict[str, Any]:
    """
    Cancel OAuth flow

    Notes: POST /oauth/cancel/:serverName
    TypeScript implementation: Directly call flowManager.failFlow()

    Process:
    1. Cancel the OAuth flow
    2. Update user connection state to DISCONNECTED
    """
    try:
        user_id = current_user.get("user_id")
        logger.info(f"[OAuth Cancel] Cancelling OAuth flow for {server_id} by user {user_id}")

        mcp_server = await server_service_v1.get_server_by_id(server_id)
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

        return {
            "success": True,
            "message": f"OAuth flow for {mcp_server.serverName} cancelled successfully",
            "server_id": server_id,
            "user_id": user_id,
            "server_name": mcp_server.serverName,
        }
    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"Failed to cancel OAuth flow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to cancel flow: {str(e)}"
        )


@router.post("/oauth/refresh/{server_id}")
async def refresh_oauth_tokens(
    server_id: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> dict[str, Any]:
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
        user_id = current_user.get("user_id")
        logger.info(f"[OAuth Refresh] Refreshing OAuth tokens for {server_id} by user {user_id}")
        mcp_server = await server_service_v1.get_server_by_id(server_id)
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
            reconnection_manager = get_reconnection_manager(
                mcp_service=mcp_service, oauth_service=mcp_service.oauth_service
            )
            reconnection_manager.clear_reconnection(user_id, server_id)
            logger.debug(f"[OAuth Refresh] Cleared reconnection attempts for {server_id}")
        except Exception as e:
            logger.warning(f"[OAuth Refresh] Could not clear reconnection (manager not initialized): {e}")

        return {
            "success": True,
            "message": f"Tokens refreshed successfully for {mcp_server.serverName}",
            "server_id": server_id,
            "user_id": user_id,
            "server_name": mcp_server.serverName,
        }
    except HTTPException:
        # Re-raise HTTP exceptions with their original status code
        raise
    except Exception as e:
        logger.error(f"Failed to refresh OAuth tokens: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to refresh tokens: {str(e)}"
        )


@router.delete("/oauth/token/{server_id}")
async def delete_oauth_tokens(
    server_id: str, current_user: CurrentUser, mcp_service: MCPService = Depends(get_mcp_service)
) -> dict[str, Any]:
    """
    Delete the OAuth token for this user

    """
    server = await server_service_v1.get_server_by_id(server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    user_id = current_user.get("user_id")
    try:
        disconnected = await mcp_service.connection_service.disconnect_user_connection(user_id, server_id)
        if disconnected:
            logger.info(f"[Delete OAuth Tokens] Disconnected {server_id} for user {user_id}")
        results = await token_service.delete_oauth_tokens(user_id, server.serverName)
        logger.info(f"[Delete OAuth Tokens] Deleted OAuth tokens for {server_id}, results: {results}")
        message = "successfully" if results else "failed"
        return {
            "success": results,
            "message": f"oauth delete {message} for {server_id}",
            "server_id": server_id,
            "user_id": user_id,
        }
    except HTTPException as e:
        logger.error(f"Failed to delete OAuth tokens: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete tokens: {str(e)}"
        )


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
    host = REGISTRY_CONSTANTS.REGISTRY_CLIENT_URL
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
