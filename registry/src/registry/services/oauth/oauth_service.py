import logging
from typing import Any

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

from ...auth.oauth import FlowStateManager, parse_scope
from ...auth.oauth.oauth_client import OAuthClient
from ...auth.oauth.oauth_utils import get_default_redirect_uri
from ...auth.oauth.types import StateMetadata
from ...schemas.enums import OAuthFlowStatus
from ...schemas.oauth_schema import (
    OAuthTokens,
)
from ...services.oauth.token_service import TokenService
from ...utils.crypto_utils import decrypt_auth_fields

logger = logging.getLogger(__name__)


class MCPOAuthService:
    """
    MCP OAuth service, referencing TypeScript MCPOAuthHandler

    Notes: MCPOAuthHandler class

    """

    def __init__(
        self,
        flow_manager: FlowStateManager,
        token_service_instance: TokenService,
    ):
        self.flow_manager = flow_manager
        self.token_service = token_service_instance
        self.oauth_client = OAuthClient()

    def _merge_oauth_config(
        self, oauth_config: dict[str, Any], oauth_metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Merge OAuth configuration with discovered OAuth metadata (pure merge function).

        Merging strategy:
        - oauth_config contains manually configured values (client_id, client_secret, scope, redirect_uri)
        - oauth_metadata contains discovered values from well-known endpoints
          (authorization_endpoint, token_endpoint, issuer, scopes_supported, etc.)
        - Manual config takes precedence over discovered metadata
        - Maps discovered endpoint names to expected config keys:
          * authorization_endpoint -> authorization_url
          * token_endpoint -> token_url

        Args:
            oauth_config: Manual OAuth configuration (should be decrypted before calling)
            oauth_metadata: Discovered OAuth metadata from well-known endpoints (optional)

        Returns:
            Merged OAuth configuration dict

        Example:
            oauth_config = {
                "client_id": "abc123",
                "client_secret": "secret",
                "scope": "read write",
                "redirect_uri": "http://localhost/callback"
            }
            oauth_metadata = {
                "authorization_endpoint": "https://example.com/oauth/authorize",
                "token_endpoint": "https://example.com/oauth/token",
                "issuer": "https://example.com"
            }
            Result = {
                "client_id": "abc123",
                "client_secret": "secret",
                "scope": "read write",
                "redirect_uri": "http://localhost/callback",
                "authorization_url": "https://example.com/oauth/authorize",
                "token_url": "https://example.com/oauth/token",
                "issuer": "https://example.com"
            }
        """
        # Create a copy to avoid mutating the original
        merged_config = oauth_config.copy()

        # Merge discovered metadata if provided
        if oauth_metadata:
            # Only use discovered values if not manually configured
            if "authorization_endpoint" in oauth_metadata and not merged_config.get("authorization_url"):
                merged_config["authorization_url"] = oauth_metadata["authorization_endpoint"]

            if "token_endpoint" in oauth_metadata and not merged_config.get("token_url"):
                merged_config["token_url"] = oauth_metadata["token_endpoint"]

            if "issuer" in oauth_metadata and not merged_config.get("issuer"):
                merged_config["issuer"] = oauth_metadata["issuer"]

            # Add additional metadata fields if available
            if "scopes_supported" in oauth_metadata:
                merged_config["scopes_supported"] = oauth_metadata["scopes_supported"]

            if "grant_types_supported" in oauth_metadata:
                merged_config["grant_types_supported"] = oauth_metadata["grant_types_supported"]

            if "response_types_supported" in oauth_metadata:
                merged_config["response_types_supported"] = oauth_metadata["response_types_supported"]

            if "resource" in oauth_metadata and not merged_config.get("resource"):
                merged_config["resource"] = oauth_metadata["resource"]

        return merged_config

    async def initiate_oauth_flow(
        self,
        user_id: str,
        server: ExtendedMCPServer,
        *,
        state_metadata: StateMetadata | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Initialize OAuth flow with automatic DCR support.

        Flow:
        1. Check if client_id exists in config
           - If YES: Use static credentials (existing behavior)
           - If NO: Check for registration_endpoint
             - If available: Trigger DCR to obtain credentials
             - If not available: Return error

        2. Generate PKCE parameters
        3. Create and store OAuth flow
        4. Build authorization URL

        Notes: MCPOAuthHandler.initiateOAuthFlow()
        """
        try:
            server_id = str(server.id)
            logger.info(f"Starting OAuth flow for user={user_id}, server={server_id}")

            oauth_config = server.config.get("oauth") or {}  # Default to empty dict for DCR-only servers
            oauth_metadata = server.config.get("oauthMetadata")
            needs_dcr = not oauth_config.get("client_id")
            if needs_dcr:
                logger.info(f"[DCR] No client_id found for {server.serverName}, checking for DCR support")
                if not oauth_metadata:
                    logger.error(f"[DCR] No oauthMetadata found for {server.serverName}")
                    return (
                        None,
                        None,
                        f"Server '{server.serverName}' requires either client_id in oauth config or oauthMetadata for DCR",
                    )
                registration_endpoint = oauth_metadata.get("registration_endpoint")
                if not registration_endpoint:
                    logger.error(f"[DCR] No registration_endpoint in oauthMetadata for {server.serverName}")
                    return (
                        None,
                        None,
                        f"Server '{server.serverName}' requires client_id or dynamic client registration support",
                    )

                # Merge oauth_metadata endpoints first (needed for DCR)
                temp_config = self._merge_oauth_config(oauth_config, oauth_metadata)

                # Trigger DCR to obtain client credentials
                logger.info(f"[DCR] Initiating dynamic client registration for {server.serverName}")

                try:
                    # Build OAuth metadata for DCR
                    from ...schemas.oauth_schema import OAuthMetadata, OAuthProtectedResourceMetadata

                    metadata_obj = OAuthMetadata(
                        issuer=oauth_metadata.get("issuer"),
                        authorization_endpoint=temp_config.get("authorization_url"),
                        token_endpoint=temp_config.get("token_url"),
                        registration_endpoint=registration_endpoint,
                        scopes_supported=oauth_metadata.get("scopes_supported"),
                        response_types_supported=oauth_metadata.get("response_types_supported"),
                        grant_types_supported=oauth_metadata.get("grant_types_supported"),
                        token_endpoint_auth_methods_supported=oauth_metadata.get(
                            "token_endpoint_auth_methods_supported"
                        ),
                        code_challenge_methods_supported=oauth_metadata.get("code_challenge_methods_supported"),
                    )

                    # Resource metadata (if available)
                    resource_metadata = None
                    if oauth_metadata.get("resource"):
                        resource_metadata = OAuthProtectedResourceMetadata(
                            resource=oauth_metadata.get("resource"),
                            authorization_servers=oauth_metadata.get("authorization_servers"),
                            scopes_supported=oauth_metadata.get("scopes_supported"),
                        )

                    # Generate redirect_uri (use configured or default)
                    redirect_uri = oauth_config.get("redirect_uri") or get_default_redirect_uri(path=server.path)

                    # Call DCR endpoint
                    client_info = await self.oauth_client.register_client(
                        server_url=server.url if hasattr(server, "url") else server.path,
                        metadata=metadata_obj,
                        resource_metadata=resource_metadata,
                        redirect_uri=redirect_uri,
                        token_exchange_method=oauth_config.get("token_endpoint_auth_method"),
                    )

                    logger.info(
                        f"[DCR] Successfully registered client for {server.serverName}: "
                        f"client_id={client_info.client_id}"
                    )

                    # Store DCR credentials immediately for future use
                    metadata_dict = {
                        "issuer": oauth_metadata.get("issuer"),
                        "authorization_endpoint": temp_config.get("authorization_url"),
                        "token_endpoint": temp_config.get("token_url"),
                        "registration_endpoint": registration_endpoint,
                        "scopes_supported": oauth_metadata.get("scopes_supported", []),
                        "grant_types_supported": oauth_metadata.get(
                            "grant_types_supported", ["authorization_code", "refresh_token"]
                        ),
                        "token_endpoint_auth_methods_supported": oauth_metadata.get(
                            "token_endpoint_auth_methods_supported", []
                        ),
                        "code_challenge_methods_supported": oauth_metadata.get("code_challenge_methods_supported", []),
                        "response_types_supported": oauth_metadata.get("response_types_supported", ["code"]),
                    }

                    await self.token_service.store_oauth_client_credentials(
                        user_id=user_id,
                        service_name=server.serverName,
                        client_info=client_info,
                        metadata=metadata_dict,
                    )
                    logger.info(f"[DCR] Stored client credentials for {server.serverName}")

                    # Build oauth_config from DCR results (merge with existing config if any)
                    oauth_config = {
                        **oauth_config,  # Preserve any existing settings (scope, redirect_uri, etc.)
                        "client_id": client_info.client_id,
                        "client_secret": client_info.client_secret,
                    }
                    if client_info.redirect_uris:
                        oauth_config["redirect_uri"] = client_info.redirect_uris[0]
                    if client_info.scope:
                        oauth_config["scope"] = client_info.scope

                except Exception as dcr_error:
                    logger.error(f"[DCR] Failed to register client for {server.serverName}: {dcr_error}", exc_info=True)
                    return None, None, f"Dynamic client registration failed: {str(dcr_error)}"
            else:
                logger.debug(f"[OAuth] Using static credentials for {server.serverName}")
                # Note that decrypt_auth_fields expects the `.config` field of an ExtendedMCPServer document object.
                oauth_config = decrypt_auth_fields({"oauth": oauth_config})["oauth"]

            oauth_config = self._merge_oauth_config(oauth_config, oauth_metadata)

            # Debug logs: verify final OAuth configuration
            logger.debug(f"OAuth config keys: {list(oauth_config.keys())}")
            logger.debug(f"authorization_url: {oauth_config.get('authorization_url')}")
            logger.debug(f"token_url: {oauth_config.get('token_url')}")
            logger.debug(f"client_id: {oauth_config.get('client_id')}")
            logger.debug(f"scope: {oauth_config.get('scope')}")

            # Generate PKCE parameters using Authlib
            code_verifier = self.oauth_client.generate_code_verifier()
            code_challenge = self.oauth_client.generate_code_challenge(code_verifier)
            flow_manager = self.flow_manager
            flow_id = flow_manager.generate_flow_id(user_id, server_id)

            # Create OAuth flow metadata (using flow_id as state)
            authorization_url = oauth_config.get("authorization_url")
            flow_metadata = flow_manager.create_flow_metadata(
                server_id=server_id,
                server_name=server.serverName,
                server_path=server.path,
                user_id=user_id,
                authorization_url=authorization_url,
                code_verifier=code_verifier,
                oauth_config=oauth_config,
                flow_id=flow_id,
                state_metadata=state_metadata,
            )

            # Create OAuth flow
            flow_manager.create_flow(
                flow_id=flow_id,
                server_id=server_id,
                user_id=user_id,
                code_verifier=code_verifier,
                metadata=flow_metadata,
            )

            # Build authorization URL using Authlib
            auth_url = await self.oauth_client.build_authorization_url(
                flow_metadata=flow_metadata, code_challenge=code_challenge, flow_id=flow_id
            )

            logger.info(f"Initiated OAuth flow: {flow_id} for {user_id}/{server_id}, auth_url: {auth_url}")
            return flow_id, auth_url, None

        except Exception as e:
            logger.error(f"Failed to initiate OAuth flow: {e}", exc_info=True)
            return None, None, str(e)

    async def complete_oauth_flow(self, flow_id: str, authorization_code: str, state: str) -> tuple[bool, str | None]:
        """
        Complete OAuth flow

        Notes: MCPOAuthHandler.completeOAuthFlow()
        """
        try:
            # 1. Decode state parameter to get flow_id
            try:
                decoded_flow_id = self.flow_manager.decode_state(state)["flow_id"]
            except ValueError as e:
                logger.error(f"Failed to decode state: {e}")
                return False, "Invalid state format"

            # 2. Verify flow_id consistency
            if decoded_flow_id != flow_id:
                logger.error(f"Flow ID mismatch: decoded={decoded_flow_id}, provided={flow_id}")
                return False, "Flow ID mismatch"

            # 3. Get flow
            flow = self.flow_manager.get_flow(flow_id)
            if not flow:
                return False, f"Flow '{flow_id}' not found"

            # 4. Verify state (should match exactly, including security token)
            if flow.state != state:
                logger.error(f"State mismatch: flow.state={flow.state}, received state={state}")
                return False, "Invalid state parameter"

            logger.info(f"State validation passed for flow {flow_id} (with security token)")

            # Check if flow has expired
            flow_manager = self.flow_manager
            if flow_manager.is_flow_expired(flow):
                flow_manager.delete_flow(flow_id)
                return False, "Flow expired"

            # Get flow metadata
            if not flow.metadata:
                return False, "Flow metadata not found"

            # Exchange tokens using Authlib
            tokens = await self.oauth_client.exchange_code_for_tokens(
                flow_metadata=flow.metadata, authorization_code=authorization_code
            )

            if not tokens:
                return False, "Failed to exchange code for tokens"

            # Update flow status
            self.flow_manager.complete_flow(flow_id, tokens)

            # Persist OAuth metadata
            metadata = {}
            if flow.metadata and flow.metadata.metadata:
                metadata = {
                    "issuer": flow.metadata.metadata.issuer,
                    "authorization_endpoint": flow.metadata.metadata.authorization_endpoint,
                    "token_endpoint": flow.metadata.metadata.token_endpoint,
                    "registration_endpoint": flow.metadata.metadata.registration_endpoint,
                    "scopes_supported": flow.metadata.metadata.scopes_supported or [],
                    "grant_types_supported": flow.metadata.metadata.grant_types_supported
                    or [
                        "authorization_code",
                        "refresh_token",
                    ],
                    "token_endpoint_auth_methods_supported": flow.metadata.metadata.token_endpoint_auth_methods_supported
                    or [],
                    "code_challenge_methods_supported": flow.metadata.metadata.code_challenge_methods_supported or [],
                    "response_types_supported": flow.metadata.metadata.response_types_supported or ["code"],
                }

            # Store client credentials separately (for DCR or static config)
            if flow.metadata and flow.metadata.client_info:
                try:
                    await self.token_service.store_oauth_client_credentials(
                        user_id=flow.user_id,
                        service_name=flow.server_name,
                        client_info=flow.metadata.client_info,
                        metadata=metadata,
                    )
                    logger.info(f"Stored OAuth client credentials for {flow.user_id}/{flow.server_name}")
                except Exception as e:
                    logger.error(f"Failed to store client credentials: {e}", exc_info=True)
                    # Don't fail the flow - credentials can be retrieved from config

            # Store access/refresh tokens
            await self.token_service.store_oauth_tokens(
                user_id=flow.user_id, service_name=flow.server_name, tokens=tokens, metadata=metadata
            )
            logger.info(f"Persisted OAuth tokens to database for {flow.user_id}/{flow.server_id}")

            logger.info(f"Completed OAuth flow: {flow_id}")
            return True, None

        except Exception as e:
            logger.error(f"Failed to complete OAuth flow: {e}", exc_info=True)
            return False, str(e)

    async def get_valid_access_token(
        self,
        user_id: str,
        server: ExtendedMCPServer,
        *,
        state_metadata: StateMetadata | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Get valid access token with automatic refresh and re-authentication flow

        This method implements the complete token lifecycle:
        1. Try to use existing access token (if not expired)
        2. If expired, try to refresh using refresh token
        3. If refresh fails, return OAuth required error to initiate new flow

        Args:
            user_id: User ID
            server: MCPServer document

        Returns:
            Tuple of (access_token, auth_url, error_message)
            - (token, None, None) if token is valid or refreshed successfully
            - (None, auth_url, None) if re-authentication is needed
            - (None, None, error) if an error occurred
        """
        try:
            server_name = server.serverName

            # 1. Check if access token exists and is not expired
            is_expired = await self.token_service.is_access_token_expired(user_id, server_name)

            if not is_expired:
                tokens = await self.token_service.get_oauth_tokens(user_id, server_name)
                if tokens and tokens.access_token:
                    logger.debug(f"Using existing valid access token for {user_id}/{server_name}")
                    return tokens.access_token, None, None

            logger.info(f"Access token expired or missing for {user_id}/{server_name}, attempting refresh")

            # 2. Try refresh token if access token is expired/missing
            has_refresh = await self.token_service.has_refresh_token(user_id, server_name)

            if has_refresh:
                success, error = await self.validate_and_refresh_tokens(user_id, server)

                if success:
                    tokens = await self.token_service.get_oauth_tokens(user_id, server_name)
                    if tokens and tokens.access_token:
                        logger.info(f"Successfully refreshed access token for {user_id}/{server_name}")
                        return tokens.access_token, None, None

                logger.warning(f"Token refresh failed for {user_id}/{server_name}: {error}")
            else:
                logger.info(f"No refresh token available for {user_id}/{server_name}")

            # 3. Both access and refresh failed - initiate new OAuth flow
            logger.info(f"Initiating new OAuth flow for {user_id}/{server_name}")
            flow_id, auth_url, flow_error = await self.initiate_oauth_flow(
                user_id, server, state_metadata=state_metadata
            )

            if flow_error:
                return None, None, f"Failed to initiate OAuth flow: {flow_error}"

            return None, auth_url, None

        except Exception as e:
            logger.error(f"Error getting valid access token: {e}", exc_info=True)
            return None, None, str(e)

    async def get_tokens(self, user_id: str, server_name: str) -> OAuthTokens | None:
        """
        Get user's OAuth tokens from database

        Args:
            user_id: 用户ID
            server_name: 服务名称

        Returns:
            OAuthTokens对象或None
        """
        tokens = await self.token_service.get_oauth_tokens(user_id, server_name)
        if tokens:
            logger.debug(f"Retrieved tokens from database for {user_id}/{server_name}")
        else:
            logger.debug(f"No tokens found in database for {user_id}/{server_name}")
        return tokens

    async def get_tokens_by_flow_id(self, flow_id: str) -> OAuthTokens | None:
        """Get OAuth tokens by flow ID"""
        flow = self.flow_manager.get_flow(flow_id)
        if not flow or flow.status != OAuthFlowStatus.COMPLETED:
            return None
        return flow.tokens

    async def get_flow_status(self, flow_id: str) -> dict[str, Any]:
        """Get flow status"""
        flow = self.flow_manager.get_flow(flow_id)
        if not flow:
            return {"status": "not_found", "error": f"Flow '{flow_id}' not found"}

        return {
            "status": flow.status,
            "completed": flow.status == OAuthFlowStatus.COMPLETED,
            "failed": flow.status == OAuthFlowStatus.FAILED,
            "error": flow.error,
            "server_id": flow.server_id,
            "user_id": flow.user_id,
            "created_at": flow.created_at,
            "completed_at": flow.completed_at,
        }

    async def cancel_oauth_flow(self, user_id: str, server_id: str) -> tuple[bool, str | None]:
        """Cancel OAuth flow"""
        try:
            success = self.flow_manager.cancel_user_flow(user_id, server_id)
            if not success:
                return True, "No active OAuth flow to cancel"

            logger.info(f"Cancelled OAuth flow for {user_id}/{server_id}")
            return True, None

        except Exception as e:
            logger.error(f"Failed to cancel OAuth flow: {e}", exc_info=True)
            return False, str(e)

    async def refresh_token(
        self,
        user_id: str,
        server_id: str,
        server_name: str,
        refresh_token_value: str,
        oauth_config: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """
        Refresh OAuth token with priority to stored client credentials.

        Priority:
        1. Use stored client credentials from MongoDB (dynamic registration)
        2. Fallback to oauth_config (static credentials)

        Args:
            user_id: User ID
            server_id: Server ID
            server_name: Server name
            refresh_token_value: Refresh token value
            oauth_config: Decrypted OAuth configuration

        Returns:
            Tuple[bool, Optional[str]]: (success, error_message)
        """
        try:
            logger.info(f"[OAuth] Refreshing token for user={user_id}, server={server_id}")

            # NEW: Try to load stored client credentials first
            stored_client_info, stored_metadata = await self.token_service.get_oauth_client_credentials(
                user_id=user_id, service_name=server_name
            )

            if stored_client_info:
                logger.info(
                    f"[OAuth] Using stored client credentials for token refresh "
                    f"(client_id={stored_client_info.client_id})"
                )

                # Build oauth_config from stored credentials
                oauth_config_for_refresh = {
                    "client_id": stored_client_info.client_id,
                    "client_secret": stored_client_info.client_secret,
                    "token_url": stored_metadata.get("token_endpoint") if stored_metadata else None,
                    "scope": stored_client_info.scope,
                    "token_endpoint_auth_methods_supported": stored_metadata.get(
                        "token_endpoint_auth_methods_supported"
                    )
                    if stored_metadata
                    else None,
                }

                # Merge with original config (for token_url if not in stored metadata)
                if not oauth_config_for_refresh["token_url"]:
                    oauth_config_for_refresh["token_url"] = oauth_config.get("token_url")

                # Use stored config for refresh
                oauth_config = oauth_config_for_refresh
            else:
                logger.debug(f"[OAuth] No stored client credentials found for {server_name}, using config credentials")

            # 1. Refresh tokens via OAuth provider using Authlib
            new_tokens = await self.oauth_client.refresh_tokens(
                oauth_config=oauth_config, refresh_token=refresh_token_value
            )

            if not new_tokens:
                return False, "Token refresh failed"

            # 2. Log refresh_token rotation
            has_new_refresh = new_tokens.refresh_token is not None
            logger.info(f"[OAuth] Token refresh successful for {server_id}, refresh_token_rotated={has_new_refresh}")

            if not has_new_refresh:
                logger.debug(
                    f"[OAuth] OAuth server did not rotate refresh_token for {server_id} "
                    f"(normal for non-rotating providers)"
                )

            # 3. Store new tokens (handles rotation automatically)
            auth_url = oauth_config.get("authorization_url")
            scopes = parse_scope(oauth_config.get("scope"), default=[])

            metadata = {
                "authorization_endpoint": auth_url,
                "token_endpoint": oauth_config.get("token_url"),
                "issuer": oauth_config.get("issuer"),
                "scopes_supported": scopes,
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "response_types_supported": ["code"],
            }
            await self.token_service.store_oauth_tokens(
                user_id=user_id, service_name=server_name, tokens=new_tokens, metadata=metadata
            )
            logger.info(f"Persisted refreshed tokens to database for {user_id}/{server_name}")
            return True, None

        except Exception as e:
            logger.error(f"Failed to refresh token: {e}", exc_info=True)
            return False, str(e)

    async def validate_and_refresh_tokens(self, user_id: str, mcp_server: ExtendedMCPServer) -> tuple[bool, str | None]:
        """
        Validate and refresh OAuth tokens (with token retrieval and validation)

        This method is used by external APIs that need full validation.
        For internal use with pre-validated tokens, use refresh_token() instead.
        """
        try:
            logger.info(f"[OAuth] Validating and refreshing tokens for user={user_id}, server={mcp_server}")
            server_name = mcp_server.serverName
            server_id = str(mcp_server.id)

            # 2. Get current tokens
            current_tokens = await self.token_service.get_oauth_tokens(user_id, server_name)
            if not current_tokens or not current_tokens.refresh_token:
                return False, "No refresh token available"

            # 3. Get OAuth config, decrypt, and merge with discovered metadata
            oauth_config = mcp_server.config.get("oauth")
            if not oauth_config:
                return False, f"Server '{server_id}' OAuth configuration not found"

            # Note that decrypt_auth_fields expects the `.config` field of an ExtendedMCPServer document object.
            oauth_config = decrypt_auth_fields({"oauth": oauth_config})["oauth"]
            oauth_metadata = mcp_server.config.get("oauthMetadata")
            oauth_config = self._merge_oauth_config(oauth_config, oauth_metadata)

            # 4. Call core refresh logic
            return await self.refresh_token(
                user_id=user_id,
                server_id=server_id,
                server_name=server_name,
                refresh_token_value=current_tokens.refresh_token,
                oauth_config=oauth_config,
            )

        except Exception as e:
            logger.error(f"Failed to validate and refresh tokens: {e}", exc_info=True)
            return False, str(e)

    async def has_active_flow(self, user_id: str, server_name: str) -> bool:
        """Check if there is an active OAuth flow"""
        user_flows = self.flow_manager.get_user_flows(user_id, server_name)
        return len(user_flows) > 0

    async def has_failed_flow(self, user_id: str, server_name: str) -> bool:
        """Check if there is a failed OAuth flow"""
        user_flows = self.flow_manager.get_user_flows(user_id, server_name)
        return any(flow.status == OAuthFlowStatus.FAILED for flow in user_flows)

    async def handle_reinitialize_auth(self, user_id: str, server: ExtendedMCPServer) -> tuple[bool, dict[str, Any]]:
        """
        Handle OAuth authentication for server reinitialization

        Decision tree:
        1. Check if access_token exists
           ├─ Exists
           │  ├─ Valid → CONNECTED, return success (Step 2.1)
           │  └─ Expired
           │     ├─ refresh_token exists and valid → Refresh → CONNECTED (Step 2.2.1.2)
           │     └─ refresh_token invalid/missing → CONNECTING, return OAuth URL (Step 2.2.1.1)
           └─ Not exists
              ├─ refresh_token exists and valid → Refresh → CONNECTED (Step 3.1.2)
              └─ refresh_token invalid/missing → CONNECTING, return OAuth URL (Step 3.1.1/3.2)

        Args:
            user_id: User ID
            server: Server document containing all server configuration

        Returns:
            Tuple[bool, Dict]: (needs_connection, response_data)
                - needs_connection: True if connection should be marked as CONNECTED
                - response_data: Response content dict
        """
        server_id = str(server.id)
        server_name = server.serverName

        # Check token status
        access_token_doc, access_valid = await self.token_service.get_access_token_status(user_id, server_name)
        refresh_token_doc, refresh_valid = await self.token_service.get_refresh_token_status(user_id, server_name)

        logger.debug(
            f"[Reinitialize] Token status for {server_name}({server_id}): "
            f"access_exists={access_token_doc is not None}, "
            f"access_valid={access_valid}, "
            f"refresh_exists={refresh_token_doc is not None}, "
            f"refresh_valid={refresh_valid}"
        )

        # Branch 1: Access token exists
        if access_token_doc is not None:
            # Step 2.1: Access token is valid
            if access_valid:
                logger.info(f"[Reinitialize] Valid access token for {server_name}({server_id})")
                return True, self._build_success_response(server)

            # Step 2.2: Access token is expired
            # Step 2.2.1.2: Refresh token exists and valid
            if refresh_valid:
                logger.info(
                    f"[Reinitialize] Access token expired for {server_name}, refresh token valid, attempting refresh"
                )
                return await self._refresh_and_connect(user_id, server)

            # Step 2.2.1.1: Refresh token invalid or missing
            logger.info(
                f"[Reinitialize] Access token expired for {server_name}, no valid refresh token, initiating OAuth"
            )
            return await self._build_oauth_required_response(user_id, server)

        # Branch 2: Access token does not exist
        # Step 3.1.2: Refresh token exists and valid
        if refresh_valid:
            logger.info(
                f"[Reinitialize] No access token for {server_name}, but refresh token valid, attempting refresh"
            )
            return await self._refresh_and_connect(user_id, server)

        # Step 3.1.1 / 3.2 / Step 1: No valid tokens
        logger.info(f"[Reinitialize] No valid tokens for {server_name}, initiating OAuth")
        return await self._build_oauth_required_response(user_id, server)

    async def _refresh_and_connect(self, user_id: str, server: ExtendedMCPServer) -> tuple[bool, dict[str, Any]]:
        """
        Helper method: Refresh tokens and return success response

        Args:
            user_id: User ID
            server: Server document containing all configuration

        Returns:
            Tuple[bool, Dict]: (needs_connection, response_data)
        """
        server_id = str(server.id)
        server_name = server.serverName

        try:
            # Get refresh token value (already validated by caller)
            refresh_token_doc, _ = await self.token_service.get_refresh_token_status(user_id, server_name)
            if not refresh_token_doc:
                logger.error(f"[Reinitialize] Refresh token disappeared for {server_name}({server_id})")
                return await self._build_oauth_required_response(user_id, server)

            # Get OAuth config, decrypt, and merge with discovered metadata
            oauth_config = server.config.get("oauth")
            if not oauth_config:
                logger.error(f"[Reinitialize] OAuth config not found for {server_name}")
                return False, {
                    "success": False,
                    "message": f"OAuth configuration not found for server '{server_name}'",
                    "serverId": server_id,
                    "server_name": server_name,
                }

            # Note that decrypt_auth_fields expects the `.config` field of an ExtendedMCPServer document object.
            oauth_config = decrypt_auth_fields({"oauth": oauth_config})["oauth"]
            oauth_metadata = server.config.get("oauthMetadata")
            oauth_config = self._merge_oauth_config(oauth_config, oauth_metadata)

            # Call core refresh logic
            success, error = await self.refresh_token(
                user_id=user_id,
                server_id=server_id,
                server_name=server_name,
                refresh_token_value=refresh_token_doc.token,
                oauth_config=oauth_config,
            )

            if success:
                logger.info(f"[Reinitialize] Token refreshed successfully for {server_name}({server_id})")
                return True, self._build_success_response(server)
            else:
                # Refresh failed - need re-authorization
                logger.warning(f"[Reinitialize] Token refresh failed for {server_name}: {error}")
                return await self._build_oauth_required_response(user_id, server)

        except Exception as e:
            logger.error(f"[Reinitialize] Error in _refresh_and_connect: {e}", exc_info=True)
            return await self._build_oauth_required_response(user_id, server)

    async def _build_oauth_required_response(
        self, user_id: str, server: ExtendedMCPServer
    ) -> tuple[bool, dict[str, Any]]:
        """
        Helper method: Build response indicating OAuth is required

        Does NOT initiate OAuth flow - frontend should call /api/v1/mcp/{server_id}/oauth/initiate
        This ensures connection status remains DISCONNECTED until frontend starts OAuth

        Args:
            user_id: User ID
            server: Server document containing all configuration

        Returns:
            Tuple[bool, Dict]: (needs_connection=False, response_data indicating OAuth required)
        """
        return False, {
            "success": True,
            "message": "OAuth authorization required",
            "serverId": str(server.id),
            "server_name": server.serverName,
            "requires_oauth": server.config.get("requiresOAuth", False),
        }

    def _build_success_response(self, server: ExtendedMCPServer) -> dict[str, Any]:
        """
        Build success response for reinitialization

        Args:
            server: Server document

        Returns:
            Response data dict
        """
        return {
            "success": True,
            "message": f"Server '{server.serverName}' reinitialized successfully",
            "server_id": str(server.id),
            "server_name": server.serverName,
            "requires_oauth": server.config.get("requiresOAuth", False),
        }
