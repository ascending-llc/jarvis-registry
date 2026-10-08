import logging
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any

import httpx
from beanie import PydanticObjectId
from redis import Redis
from redis.exceptions import RedisError

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.mcp_server_oauth import RegistryOAuthClient, RegistryOAuthState

from ..core.crypto_utils import decrypt_auth_fields, decrypt_value, encrypt_value
from ..core.exceptions import DownstreamAuthRejectedException
from .discovery import DiscoveryResult, discover_mcp_oauth
from .errors import (
    OAuthDiscoveryError,
    OAuthReAuthRequiredError,
    OAuthTokenEndpointError,
    OAuthTokenError,
    RegistryOAuthStateConflictError,
)
from .flow_state_manager import FlowStateManager
from .oauth_client import OAuthClient
from .oauth_utils import get_default_redirect_uri
from .refresh_lock import REFRESH_IN_PROGRESS_MESSAGE, refresh_lock
from .schemas import (
    MCPClientContext,
    MCPOAuthFlowMetadata,
    OAuthFlowStatus,
    OAuthFlowStatusResponse,
    OAuthMetadata,
    OAuthProtectedResourceMetadata,
    OAuthTokens,
)
from .token_service import TokenService
from .types import StateMetadata

logger = logging.getLogger(__name__)

# Lost compare-and-swap writes on registryOAuth tolerated by one ensure_registry_oauth_state call.
ENSURE_STATE_MAX_ATTEMPTS = 3

INVALID_CLIENT_ERROR = "invalid_client"

# AS metadata fields whose change means the server's OAuth configuration moved (Change 9 step 4).
_DISCOVERY_IDENTITY_FIELDS = ("authorization_endpoint", "token_endpoint", "registration_endpoint")


def _not_found_flow_status() -> OAuthFlowStatusResponse:
    """Return the uniform API representation for an absent or logically expired flow."""
    return OAuthFlowStatusResponse(status="not_found", completed=False, failed=False)


def _none_if_empty(value: Any) -> Any:
    return value or None


def _scope_set(scope: str | None) -> frozenset[str]:
    return frozenset((scope or "").split())


def _token_binding(*, client_id: str | None, issuer: str | None, resource: str | None) -> dict[str, str | None]:
    """Metadata stored on Registry token records: which client and authorization server issued them."""
    return {
        "clientId": _none_if_empty(client_id),
        "issuer": _none_if_empty(issuer),
        "resource": _none_if_empty(resource),
    }


def _rejected_message(server_name: str) -> str:
    return f"{server_name} rejected a freshly issued access token; the server's OAuth configuration may be incompatible"


class MCPOAuthService:
    """
    MCP OAuth service, referencing TypeScript MCPOAuthHandler

    Registry's OAuth identity is separate from Jarvis Chat's: the per-server client and discovery
    result live in ``mcpservers.registryOAuth`` and per-user tokens under ``registry:mcp:*``.

    Notes: MCPOAuthHandler class

    """

    def __init__(
        self,
        flow_manager: FlowStateManager,
        token_service_instance: TokenService,
        *,
        registry_app_name: str,
        base_redirect_url: str,
        encryption_key: bytes,
        redis_client: Redis | None,
        redis_key_prefix: str,
    ):
        """
        Args:
            flow_manager: OAuth flow state manager
            token_service_instance: Token storage/retrieval service
            registry_app_name: Client name for dynamic client registration
                (caller-supplied; e.g. settings.registry_app_name)
            base_redirect_url: Base URL for OAuth callback redirect URIs
                (caller-supplied; e.g. settings.registry_client_url)
            encryption_key: AES key bytes for decrypting server auth config
                (caller-supplied; e.g. settings.encryption_key)
            redis_client: Redis client for the refresh lock (a process-local lock is used when
                None, or when the flow manager fell back to memory storage)
            redis_key_prefix: Namespace prefix for the refresh lock key (e.g. settings.redis_key_prefix)
        """
        self.flow_manager = flow_manager
        self.token_service = token_service_instance
        self.oauth_client = OAuthClient(registry_app_name)
        self._registry_app_name = registry_app_name
        self._base_redirect_url = base_redirect_url
        self._encryption_key = encryption_key
        self._lock_redis_client = redis_client if getattr(flow_manager, "uses_redis", False) else None
        self._redis_key_prefix = redis_key_prefix

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

            if oauth_metadata.get("resource") and not merged_config.get("resource"):
                merged_config["resource"] = oauth_metadata["resource"]

        return merged_config

    # ------------------------------------------------------------------
    # registryOAuth: discovery, client and compare-and-swap persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _is_static_client(server: ExtendedMCPServer) -> bool:
        return bool((server.config.get("oauth") or {}).get("client_id"))

    def _redirect_uri(self, server: ExtendedMCPServer) -> str:
        return get_default_redirect_uri(path=server.path or "", base_url=self._base_redirect_url)

    @staticmethod
    def _server_url(server: ExtendedMCPServer) -> str:
        return (server.config or {}).get("url") or ""

    @staticmethod
    def _usable_state(state: RegistryOAuthState | None, server_url: str) -> RegistryOAuthState | None:
        """A state bound to another URL is treated exactly like an absent one."""
        if state is None or not state.is_bound_to(server_url):
            return None
        return state

    async def _run_discovery(self, server_url: str, *, www_authenticate: str | None = None) -> DiscoveryResult:
        if not server_url:
            raise OAuthDiscoveryError("Server has no URL to discover OAuth metadata for")
        # Follow redirects like the mcp SDK's own client: providers may redirect well-known documents.
        async with httpx.AsyncClient(
            headers={"User-Agent": self._registry_app_name}, follow_redirects=True
        ) as http_client:
            return await discover_mcp_oauth(server_url, http_client=http_client, www_authenticate=www_authenticate)

    @staticmethod
    def _client_survives(
        bound_state: RegistryOAuthState, client: RegistryOAuthClient, discovery: DiscoveryResult, redirect_uri: str
    ) -> bool:
        """Whether a client registered under ``bound_state`` still fits ``discovery`` (Change 4 step 2)."""
        old_metadata = bound_state.authorizationServerMetadata or {}
        new_metadata = discovery.authorization_server_metadata
        expires_at = client.clientSecretExpiresAt
        if expires_at is not None:
            # pymongo returns naive datetimes (holding UTC) because the client isn't tz_aware.
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                return False
        return (
            _none_if_empty(bound_state.issuer) == _none_if_empty(discovery.issuer)
            and old_metadata.get("registration_endpoint") == new_metadata.get("registration_endpoint")
            and _none_if_empty(bound_state.resource) == _none_if_empty(discovery.resource)
            and _scope_set(client.scope) == _scope_set(discovery.scope)
            and client.redirectUri == redirect_uri
        )

    async def _register_client(
        self, server: ExtendedMCPServer, discovery: DiscoveryResult, redirect_uri: str
    ) -> RegistryOAuthClient:
        """Register one Registry client for the server through DCR (RFC 7591)."""
        as_metadata = discovery.authorization_server_metadata
        registration_endpoint = as_metadata.get("registration_endpoint")
        if not registration_endpoint:
            raise ValueError(f"Server '{server.serverName}' requires client_id or dynamic client registration support")

        metadata_obj = OAuthMetadata(
            issuer=discovery.issuer,
            authorization_endpoint=as_metadata["authorization_endpoint"],
            token_endpoint=as_metadata["token_endpoint"],
            registration_endpoint=registration_endpoint,
            scopes_supported=as_metadata.get("scopes_supported"),
            response_types_supported=as_metadata.get("response_types_supported"),
            grant_types_supported=as_metadata.get("grant_types_supported"),
            token_endpoint_auth_methods_supported=as_metadata.get("token_endpoint_auth_methods_supported"),
            code_challenge_methods_supported=as_metadata.get("code_challenge_methods_supported"),
        )
        resource_metadata = None
        if discovery.resource:
            resource_metadata = OAuthProtectedResourceMetadata(
                resource=discovery.resource,
                scopes_supported=(discovery.protected_resource_metadata or {}).get("scopes_supported"),
            )

        oauth_config = server.config.get("oauth") or {}
        logger.info(f"[DCR] Registering Registry OAuth client for {server.serverName}")
        client_info = await self.oauth_client.register_client(
            server_url=self._server_url(server),
            metadata=metadata_obj,
            resource_metadata=resource_metadata,
            redirect_uri=redirect_uri,
            token_exchange_method=oauth_config.get("token_endpoint_auth_method"),
            scope=discovery.scope,
        )
        logger.info(f"[DCR] Registered client for {server.serverName}: client_id={client_info.client_id}")

        expires_at = client_info.client_secret_expires_at
        return RegistryOAuthClient(
            clientId=client_info.client_id,
            clientSecret=(
                encrypt_value(client_info.client_secret, encryption_key=self._encryption_key)
                if client_info.client_secret
                else None
            ),
            redirectUri=redirect_uri,
            tokenEndpointAuthMethod=client_info.token_endpoint_auth_method or "client_secret_basic",
            scope=discovery.scope,
            registeredAt=datetime.now(UTC),
            clientSecretExpiresAt=datetime.fromtimestamp(expires_at, UTC) if expires_at else None,
        )

    @staticmethod
    def _new_state(
        server_url: str, discovery: DiscoveryResult | None, client: RegistryOAuthClient | None
    ) -> RegistryOAuthState:
        """Build a state from a discovery result, or the failed-discovery state when it is None."""
        return RegistryOAuthState(
            revision=str(uuid.uuid4()),
            serverUrl=server_url,
            resource=discovery.resource if discovery else None,
            issuer=discovery.issuer if discovery else None,
            protectedResourceMetadata=discovery.protected_resource_metadata if discovery else None,
            authorizationServerMetadata=discovery.authorization_server_metadata if discovery else None,
            scope=discovery.scope if discovery else None,
            discoveredAt=datetime.now(UTC),
            client=client,
        )

    async def _save_registry_oauth_state(
        self, server: ExtendedMCPServer, new_state: RegistryOAuthState, expected_revision: str | None
    ) -> bool:
        """Compare-and-swap write of ``registryOAuth``; returns whether it matched.

        Beanie's ``use_revision`` can't be used: it guards the whole document and Chat doesn't
        maintain ``revision_id``. ``registryOAuth`` is excluded from Beanie writes, so this is its
        only writer apart from ``invalidate_registry_oauth_client`` and server_service's ``$unset``.
        """
        if expected_revision is None:
            query: dict[str, Any] = {"_id": server.id, "registryOAuth": {"$exists": False}}
        else:
            query = {"_id": server.id, "registryOAuth.revision": expected_revision}
        result = await ExtendedMCPServer.get_pymongo_collection().update_one(
            query, {"$set": {"registryOAuth": new_state.model_dump()}}
        )
        if result.matched_count == 0:
            return False
        server.registryOAuth = new_state
        return True

    async def _reload_registry_oauth_state(self, server: ExtendedMCPServer) -> RegistryOAuthState | None:
        document = await ExtendedMCPServer.get_pymongo_collection().find_one({"_id": server.id}, {"registryOAuth": 1})
        raw = (document or {}).get("registryOAuth")
        server.registryOAuth = RegistryOAuthState.model_validate(raw) if raw else None
        return server.registryOAuth

    async def ensure_registry_oauth_state(
        self,
        server: ExtendedMCPServer,
        *,
        force_discovery: bool,
        discovery: DiscoveryResult | None = None,
    ) -> RegistryOAuthState:
        """Return the server's effective ``registryOAuth``, discovering and registering as needed.

        ``force_discovery=True`` is the login mode: discovery always runs and a DCR server without a
        surviving client registers one. ``force_discovery=False`` is the refresh mode: discovery runs
        only without a usable state, and no client is ever registered (a new client can't use a
        refresh token issued to another one). ``discovery`` passes in a result the caller already has.

        Raises:
            OAuthDiscoveryError: Discovery failed for a DCR server.
            RegistryOAuthStateConflictError: ``ENSURE_STATE_MAX_ATTEMPTS`` writes lost their race.
        """
        server_url = self._server_url(server)
        static = self._is_static_client(server)
        redirect_uri = self._redirect_uri(server)
        stored = self._usable_state(server.registryOAuth, server_url)

        if discovery is None and not force_discovery and stored is not None:
            return stored

        discovery_failed = False
        if discovery is None:
            try:
                discovery = await self._run_discovery(server_url)
            except OAuthDiscoveryError as e:
                if not static:
                    raise
                logger.warning(f"OAuth discovery failed for static-client server {server.serverName}: {e}")
                discovery_failed = True

        registered_client: RegistryOAuthClient | None = None
        for _attempt in range(ENSURE_STATE_MAX_ATTEMPTS):
            if discovery_failed or discovery is None:
                # Keep a good state over one failed discovery; it fills gaps in config.oauth.
                if stored is not None and stored.issuer is not None:
                    return stored
                new_state = self._new_state(server_url, None, None)
            else:
                client: RegistryOAuthClient | None = None
                if not static:
                    # Candidates: the stored (on retries, the winner's) client, then one this call
                    # registered. A client only read from an earlier state is never written back.
                    if (
                        stored is not None
                        and stored.client is not None
                        and self._client_survives(stored, stored.client, discovery, redirect_uri)
                    ):
                        client = stored.client
                    elif registered_client is not None:
                        client = registered_client
                    elif force_discovery:
                        registered_client = await self._register_client(server, discovery, redirect_uri)
                        client = registered_client
                new_state = self._new_state(server_url, discovery, client)

            expected_revision = server.registryOAuth.revision if server.registryOAuth else None
            if await self._save_registry_oauth_state(server, new_state, expected_revision):
                return new_state

            logger.info(f"Lost registryOAuth write race for {server.serverName}; re-reading the winner")
            winner = self._usable_state(await self._reload_registry_oauth_state(server), server_url)
            if not force_discovery and winner is not None:
                return winner
            stored = winner

        raise RegistryOAuthStateConflictError(
            f"Could not persist OAuth state for {server.serverName} after {ENSURE_STATE_MAX_ATTEMPTS} attempts"
        )

    async def _invalidate_client(self, server_id: PydanticObjectId | str, rejected_client_id: str) -> bool:
        result = await ExtendedMCPServer.get_pymongo_collection().update_one(
            {"_id": PydanticObjectId(server_id), "registryOAuth.client.clientId": rejected_client_id},
            {"$set": {"registryOAuth.client": None, "registryOAuth.revision": str(uuid.uuid4())}},
        )
        if result.matched_count:
            logger.warning(f"Cleared Registry OAuth client {rejected_client_id} of server {server_id} (invalid_client)")
        return bool(result.matched_count)

    async def invalidate_registry_oauth_client(self, server: ExtendedMCPServer, rejected_client_id: str) -> bool:
        """Clear ``registryOAuth.client`` only if it is still ``rejected_client_id``.

        Filters on the client id rather than a revision: callers don't hold the revision their
        client was read at, and the current one may belong to a newer client another login registered.
        """
        cleared = await self._invalidate_client(server.id, rejected_client_id)
        if cleared and server.registryOAuth is not None:
            server.registryOAuth = await self._reload_registry_oauth_state(server)
        return cleared

    def _resolve_oauth_config(self, server: ExtendedMCPServer, state: RegistryOAuthState) -> dict[str, Any] | None:
        """Client credentials and endpoints for the server, or None for a DCR server without a client.

        Static ``config.oauth`` wins over ``registryOAuth.client``; manual ``config.oauth`` URLs win
        over discovered endpoints.
        """
        static_config = server.config.get("oauth") or {}
        if static_config.get("client_id"):
            # Note that decrypt_auth_fields expects the `.config` field of an ExtendedMCPServer document object.
            oauth_config = decrypt_auth_fields({"oauth": static_config}, encryption_key=self._encryption_key)["oauth"]
            # Strip like FlowStateManager._create_client_info, so the client_id a refresh sends and
            # binds on equals the one the login used and stored in the token binding.
            oauth_config["client_id"] = str(oauth_config["client_id"]).strip()
        else:
            client = state.client
            if client is None:
                return None
            oauth_config = {
                **static_config,
                "client_id": client.clientId,
                "client_secret": (
                    decrypt_value(client.clientSecret, encryption_key=self._encryption_key)
                    if client.clientSecret
                    else None
                ),
                "scope": state.scope,
                # Authenticate at the token endpoint the way the client was registered.
                "token_endpoint_auth_methods_supported": [client.tokenEndpointAuthMethod],
            }

        metadata = dict(state.authorizationServerMetadata or {})
        if state.resource:
            metadata["resource"] = state.resource
        return self._merge_oauth_config(oauth_config, metadata)

    def _binding_matches(self, metadata: dict[str, Any] | None, expected: dict[str, str | None], static: bool) -> bool:
        """Static-client servers bind on clientId only: their AS is set by hand in config.oauth."""
        metadata = metadata or {}
        keys = ("clientId",) if static else ("clientId", "issuer", "resource")
        return all(_none_if_empty(metadata.get(key)) == _none_if_empty(expected.get(key)) for key in keys)

    @staticmethod
    def _access_binding_matches(metadata: dict[str, Any] | None, state: RegistryOAuthState) -> bool:
        """DCR use-time check: issuer and resource only. A re-registered client doesn't invalidate
        bearer tokens at the resource, so clientId is deliberately not compared."""
        metadata = metadata or {}
        return _none_if_empty(metadata.get("issuer")) == _none_if_empty(state.issuer) and _none_if_empty(
            metadata.get("resource")
        ) == _none_if_empty(state.resource)

    def _refresh_lock(self, user_id: str, server_id: str):
        return refresh_lock(
            self._lock_redis_client, key_prefix=self._redis_key_prefix, user_id=user_id, server_id=server_id
        )

    # ------------------------------------------------------------------
    # Interactive login
    # ------------------------------------------------------------------

    async def initiate_oauth_flow(
        self,
        user_id: str,
        server: ExtendedMCPServer,
        *,
        state_metadata: StateMetadata | None = None,
        mcp_client_context: MCPClientContext | None = None,
        device_code: str | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Initialize an OAuth flow.

        Flow:
        1. Re-run discovery and resolve ``registryOAuth`` (registering a DCR client if needed)
        2. Choose the client: static ``config.oauth`` if it has client_id, else Registry's client
        3. Generate PKCE parameters, create and store the flow
        4. Build the authorization URL (with the RFC 8707 resource when discovered)

        Notes: MCPOAuthHandler.initiateOAuthFlow()
        """
        try:
            server_id = str(server.id)
            logger.info(f"Starting OAuth flow for user={user_id}, server={server_id}")

            state = await self.ensure_registry_oauth_state(server, force_discovery=True)
            oauth_config = self._resolve_oauth_config(server, state)
            if oauth_config is None:
                return None, None, f"No OAuth client available for server '{server.serverName}'"

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
                redirect_uri=self._redirect_uri(server),
                state_metadata=state_metadata,
                mcp_client_context=mcp_client_context,
                device_code=device_code,
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

    @staticmethod
    def _flow_binding(flow_metadata: MCPOAuthFlowMetadata) -> dict[str, str | None]:
        """Binding of tokens from a flow: the client, AS and resource the login started with."""
        return _token_binding(
            client_id=flow_metadata.client_info.client_id,
            issuer=flow_metadata.metadata.issuer if flow_metadata.metadata else None,
            resource=flow_metadata.resource_metadata.resource if flow_metadata.resource_metadata else None,
        )

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
            try:
                tokens = await self.oauth_client.exchange_code_for_tokens(
                    flow_metadata=flow.metadata, authorization_code=authorization_code
                )
            except OAuthTokenEndpointError as e:
                if e.error_code == INVALID_CLIENT_ERROR:
                    # The provider deleted our registration; the next login registers a new client.
                    await self._invalidate_client(flow.server_id, flow.metadata.client_info.client_id)
                tokens = None

            if not tokens:
                return False, "Failed to exchange code for tokens"

            # Update flow status
            self.flow_manager.complete_flow(flow_id, tokens)

            # Store under the refresh lock so an in-flight refresh with the old client can't overwrite
            # these tokens after this write.
            await self._store_login_tokens(flow.user_id, flow.server_id, flow.server_name, tokens, flow.metadata)
            logger.info(f"Persisted OAuth tokens to database for {flow.user_id}/{flow.server_id}")

            logger.info(f"Completed OAuth flow: {flow_id}")
            return True, None

        except Exception as e:
            logger.error(f"Failed to complete OAuth flow: {e}", exc_info=True)
            return False, str(e)

    async def _store_login_tokens(
        self,
        user_id: str,
        server_id: str,
        server_name: str,
        tokens: OAuthTokens,
        flow_metadata: MCPOAuthFlowMetadata,
    ) -> None:
        binding = self._flow_binding(flow_metadata)
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(self._refresh_lock(user_id, server_id))
            except (OAuthTokenError, RedisError) as e:
                # The code is already spent, and these are the newest tokens; the refresh side
                # re-checks before writing.
                logger.warning(f"Storing login tokens for {user_id}/{server_name} without the refresh lock: {e}")
            await self.token_service.store_oauth_tokens(
                user_id=user_id, service_name=server_name, tokens=tokens, metadata=binding
            )

    async def get_valid_access_token(
        self,
        user_id: str,
        server: ExtendedMCPServer,
        *,
        state_metadata: StateMetadata | None = None,
        interactive: bool = True,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Get valid access token with automatic refresh and re-authentication flow

        This method implements the complete token lifecycle:
        1. Try to use existing access token (if not expired and, for DCR servers, still bound to
           the current authorization server and resource)
        2. If expired, try to refresh using refresh token
        3. If refresh fails, initiate a new OAuth flow (interactive callers) or raise
           OAuthReAuthRequiredError directly (non-interactive callers)

        Args:
            user_id: User ID
            server: MCPServer document
            interactive: When False (e.g. scheduled workflow runs with no human to complete
                a flow), skip initiate_oauth_flow entirely and raise OAuthReAuthRequiredError
                with auth_url=None instead of minting a Redis flow record no one can complete.

        Returns:
            Tuple of (access_token, auth_url, error_message)
            - (token, None, None) if token is valid or refreshed successfully
            - (None, auth_url, None) if re-authentication is needed (interactive only)
            - (None, None, error) if an error occurred
        """
        try:
            server_name = server.serverName

            # 1. Use a stored, unexpired access token if it is still bound to this server's AS.
            access_doc = await self.token_service.get_oauth_access_token(user_id, server_name)
            if access_doc is not None and access_doc.token:
                if self._is_static_client(server):
                    logger.debug(f"Using existing valid access token for {user_id}/{server_name}")
                    return access_doc.token, None, None
                try:
                    state = await self.ensure_registry_oauth_state(server, force_discovery=False)
                except OAuthDiscoveryError as e:
                    # Refresh and login would both run discovery again; fail once instead.
                    logger.warning(f"OAuth state unavailable for {server_name}: {e}")
                    return None, None, f"OAuth discovery failed for {server_name}: {e}"
                if self._access_binding_matches(access_doc.metadata, state):
                    logger.debug(f"Using existing valid access token for {user_id}/{server_name}")
                    return access_doc.token, None, None
                logger.info(f"Access token for {user_id}/{server_name} is bound to an old authorization server")
            else:
                logger.info(f"Access token expired or missing for {user_id}/{server_name}, attempting refresh")

            # 2. Try refresh token if access token is expired/missing/stale
            has_refresh = await self.token_service.has_refresh_token(user_id, server_name)

            if has_refresh:
                success, error = await self.validate_and_refresh_tokens(user_id, server)

                if success:
                    refreshed = await self.token_service.get_oauth_access_token(user_id, server_name)
                    if refreshed is not None and refreshed.token:
                        logger.info(f"Successfully refreshed access token for {user_id}/{server_name}")
                        return refreshed.token, None, None

                logger.warning(f"Token refresh failed for {user_id}/{server_name}: {error}")
            else:
                logger.info(f"No refresh token available for {user_id}/{server_name}")

            # 3. Both access and refresh failed.
            if not interactive:
                logger.info(
                    f"Re-authorization required for {user_id}/{server_name} (non-interactive; no flow initiated)"
                )
                raise OAuthReAuthRequiredError(
                    f"OAuth re-authentication required for {server_name} (non-interactive caller)",
                    auth_url=None,
                    server_name=server_name,
                )

            # Interactive caller: initiate a new OAuth flow to mint an auth_url.
            logger.info(f"Initiating new OAuth flow for {user_id}/{server_name}")
            flow_id, auth_url, flow_error = await self.initiate_oauth_flow(
                user_id, server, state_metadata=state_metadata
            )

            if flow_error:
                return None, None, f"Failed to initiate OAuth flow: {flow_error}"

            return None, auth_url, None

        except OAuthReAuthRequiredError:
            # Non-interactive re-auth signal must propagate, not be flattened into an error tuple.
            raise
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

    async def get_flow_status(self, flow_id: str) -> OAuthFlowStatusResponse:
        """Return a flow's status, lazily expiring unfinished records by their logical TTL."""
        flow = self.flow_manager.get_flow(flow_id)
        if not flow:
            return _not_found_flow_status()

        if flow.status != OAuthFlowStatus.COMPLETED and self.flow_manager.is_flow_expired(flow):
            self.flow_manager.delete_flow(flow_id)
            return _not_found_flow_status()

        return OAuthFlowStatusResponse(
            status=flow.status,
            completed=flow.status == OAuthFlowStatus.COMPLETED,
            failed=flow.status == OAuthFlowStatus.FAILED,
            error=flow.error,
            server_id=flow.server_id,
            user_id=flow.user_id,
            created_at=flow.created_at,
            completed_at=flow.completed_at,
        )

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

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def _fresh_access_token_available(
        self,
        user_id: str,
        server_name: str,
        rejected_access_token: str | None,
        state: RegistryOAuthState,
        static: bool,
    ) -> bool:
        """Whether another refresh already stored a usable access token (compared by value: the
        Token model has no updatedAt and records are updated in place)."""
        access_doc = await self.token_service.get_oauth_access_token(user_id, server_name)
        if access_doc is None or not access_doc.token or access_doc.token == rejected_access_token:
            return False
        return static or self._access_binding_matches(access_doc.metadata, state)

    async def _refresh_with_lock(
        self, user_id: str, server: ExtendedMCPServer, *, rejected_access_token: str | None = None
    ) -> tuple[bool, str | None]:
        """Refresh the user's tokens for the server; every refresh goes through here.

        Args:
            rejected_access_token: The token a downstream server just answered 401 to, or None
                when the refresh was triggered by expiry.

        Returns:
            (success, error_message). A failure leads the caller to an interactive login.
        """
        server_name = server.serverName
        static = self._is_static_client(server)

        # 1. Resolve state without forcing discovery, and the client to refresh with.
        try:
            state = await self.ensure_registry_oauth_state(server, force_discovery=False)
        except OAuthDiscoveryError as e:
            return False, str(e)
        oauth_config = self._resolve_oauth_config(server, state)
        if oauth_config is None:
            return False, f"No OAuth client registered for server '{server_name}'; login required"
        binding = _token_binding(
            client_id=oauth_config.get("client_id"),
            issuer=state.issuer if not static else oauth_config.get("issuer"),
            resource=state.resource if not static else oauth_config.get("resource"),
        )

        # 2. The refresh token must belong to the current client (and, for DCR, AS and resource).
        refresh_doc = await self.token_service.get_oauth_refresh_token(user_id, server_name)
        if refresh_doc is None or not refresh_doc.token:
            return False, "No refresh token available"
        if not self._binding_matches(refresh_doc.metadata, binding, static):
            return False, "Refresh token is bound to a different OAuth client or authorization server"

        # 3./4. Serialize refreshes for this user and server across pods.
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(self._refresh_lock(user_id, str(server.id)))
            except OAuthTokenError:
                if await self._fresh_access_token_available(user_id, server_name, rejected_access_token, state, static):
                    return True, None
                return False, REFRESH_IN_PROGRESS_MESSAGE
            except RedisError as e:
                logger.warning(f"Refresh lock unavailable for {user_id}/{server_name}: {e}")
                return False, f"Refresh lock unavailable: {e}"

            if await self._fresh_access_token_available(user_id, server_name, rejected_access_token, state, static):
                logger.info(f"Another refresh already stored a new access token for {user_id}/{server_name}")
                return True, None

            # 5. Re-read under the lock: the record read above may already have been rotated away.
            refresh_doc = await self.token_service.get_oauth_refresh_token(user_id, server_name)
            if refresh_doc is None or not refresh_doc.token:
                return False, "No refresh token available"
            if not self._binding_matches(refresh_doc.metadata, binding, static):
                return False, "Refresh token is bound to a different OAuth client or authorization server"
            used_refresh_token = refresh_doc.token

            try:
                new_tokens = await self.oauth_client.refresh_tokens(
                    oauth_config, used_refresh_token, resource=state.resource
                )
            except OAuthTokenEndpointError as e:
                if e.error_code == INVALID_CLIENT_ERROR and not static:
                    await self.invalidate_registry_oauth_client(server, oauth_config["client_id"])
                return False, f"Token refresh failed: {e.error_code or e}"
            if not new_tokens:
                return False, "Token refresh failed"

            # 6. A login may have stored newer tokens while the token endpoint call outlasted the lock.
            current = await self.token_service.get_oauth_refresh_token(user_id, server_name)
            if (
                current is None
                or current.token != used_refresh_token
                or not self._binding_matches(current.metadata, binding, static)
            ):
                logger.info(f"Newer tokens were stored for {user_id}/{server_name} during refresh; keeping them")
                return True, None

            logger.info(
                f"[OAuth] Token refresh successful for {server_name}, "
                f"refresh_token_rotated={new_tokens.refresh_token != used_refresh_token}"
            )
            await self.token_service.store_oauth_tokens(
                user_id=user_id, service_name=server_name, tokens=new_tokens, metadata=binding
            )
            logger.info(f"Persisted refreshed tokens to database for {user_id}/{server_name}")
            return True, None

    async def validate_and_refresh_tokens(self, user_id: str, mcp_server: ExtendedMCPServer) -> tuple[bool, str | None]:
        """
        Validate and refresh OAuth tokens.

        With an unexpired access token another refresh already stored, this returns success without
        calling the token endpoint.
        """
        try:
            logger.info(f"[OAuth] Validating and refreshing tokens for user={user_id}, server={mcp_server.serverName}")
            if not await self.token_service.has_refresh_token(user_id, mcp_server.serverName):
                return False, "No refresh token available"
            return await self._refresh_with_lock(user_id, mcp_server)

        except Exception as e:
            logger.error(f"Failed to validate and refresh tokens: {e}", exc_info=True)
            return False, str(e)

    # ------------------------------------------------------------------
    # Downstream 401 recovery
    # ------------------------------------------------------------------

    def _discovery_changed(self, server: ExtendedMCPServer, discovery: DiscoveryResult) -> bool:
        stored = self._usable_state(server.registryOAuth, self._server_url(server))
        if stored is None:
            return True
        old_metadata = stored.authorizationServerMetadata or {}
        new_metadata = discovery.authorization_server_metadata
        return (
            _none_if_empty(stored.issuer) != _none_if_empty(discovery.issuer)
            or _none_if_empty(stored.resource) != _none_if_empty(discovery.resource)
            or any(old_metadata.get(field) != new_metadata.get(field) for field in _DISCOVERY_IDENTITY_FIELDS)
        )

    async def recover_from_unauthorized(
        self,
        user_id: str,
        server: ExtendedMCPServer,
        *,
        rejected_access_token: str,
        www_authenticate: str | None,
        is_retry: bool,
    ) -> str | None:
        """Decide what to do after a downstream 401.

        Args:
            rejected_access_token: The bearer token the downstream server answered 401 to.
            www_authenticate: The downstream 401's WWW-Authenticate header.
            is_retry: False for the 401 on the original call, True for the 401 on the single retry.

        Returns:
            A refreshed access token to retry once with, or None: the caller then rebuilds its
            headers, which starts a login (interactive) or raises OAuthReAuthRequiredError.

        Raises:
            DownstreamAuthRejectedException: A freshly issued token was rejected and nothing about
                the server's OAuth configuration changed. This is the login-loop guard.
        """
        server_name = server.serverName
        static = self._is_static_client(server)

        if is_retry and static:
            # Their AS is set by config.oauth; a login would get a token rejected the same way.
            raise DownstreamAuthRejectedException(_rejected_message(server_name))

        # Without this, get_valid_access_token would keep returning the stored, rejected token.
        await self.token_service.delete_access_token_if_matches(user_id, server_name, rejected_access_token)

        if not is_retry:
            success, error = await self._refresh_with_lock(user_id, server, rejected_access_token=rejected_access_token)
            if not success:
                logger.info(f"Refresh after downstream 401 failed for {user_id}/{server_name}: {error}")
                return None
            refreshed = await self.token_service.get_oauth_access_token(user_id, server_name)
            if refreshed is None or not refreshed.token or refreshed.token == rejected_access_token:
                return None
            return refreshed.token

        try:
            discovery = await self._run_discovery(self._server_url(server), www_authenticate=www_authenticate)
        except OAuthDiscoveryError as e:
            raise DownstreamAuthRejectedException(_rejected_message(server_name)) from e

        if not self._discovery_changed(server, discovery):
            raise DownstreamAuthRejectedException(_rejected_message(server_name))

        logger.info(f"OAuth discovery for {server_name} changed after a downstream 401; a new login is required")
        try:
            await self.ensure_registry_oauth_state(server, force_discovery=True, discovery=discovery)
        except (RegistryOAuthStateConflictError, httpx.HTTPError, ValueError) as e:
            # The login that follows re-runs discovery and registration.
            logger.warning(f"Could not persist the changed OAuth discovery for {server_name}: {e}")
        await self.token_service.delete_oauth_tokens(user_id, server_name)
        return None

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
           │     └─ refresh_token invalid/missing → DISCONNECTED, return `oauth_required=True` (no flow created) (Step 2.2.1.1)
           └─ Not exists
              ├─ refresh_token exists and valid → Refresh → CONNECTED (Step 3.1.2)
              └─ refresh_token invalid/missing → DISCONNECTED, return `oauth_required=True` (no flow created) (Step 3.1.1/3.2)

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
            success, error = await self._refresh_with_lock(user_id, server)

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
            "oauth_required": True,
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
            "oauth_required": False,
        }
