import asyncio
import base64
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

from .errors import OAuthFlowStorageError
from .oauth_utils import parse_scope, scope_to_string
from .redis_flow_storage import RedisFlowStorage
from .schemas import (
    MCPClientContext,
    MCPOAuthFlowMetadata,
    OAuthClientInformation,
    OAuthFlow,
    OAuthFlowStatus,
    OAuthMetadata,
    OAuthProtectedResourceMetadata,
    OAuthTokens,
)
from .types import OAuthFlowState, StateMetadata

logger = logging.getLogger(__name__)

_FLOW_STORAGE_CONCURRENCY = 8


class FlowStateManager:
    """
    OAuth Flow State Manager
    """

    DEFAULT_FLOW_TTL = 600  # Flow time-to-live in seconds (10 minutes)

    def __init__(self, redis_client: Redis, *, redis_key_prefix: str) -> None:
        if redis_client is None:
            raise RuntimeError("FlowStateManager requires Redis")
        self._flow_ttl = self.DEFAULT_FLOW_TTL
        self._redis_storage = RedisFlowStorage(redis_client, redis_key_prefix=redis_key_prefix)
        self._io_limit = asyncio.Semaphore(_FLOW_STORAGE_CONCURRENCY)

    async def _run_storage[T](self, operation: Callable[..., T], *args: Any) -> T:
        # Keep the slot until the thread finishes, even if the caller is cancelled.
        async with self._io_limit:
            task = asyncio.create_task(asyncio.to_thread(operation, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await task
                finally:
                    raise

    async def initialize(self) -> None:
        try:
            await self._run_storage(self._redis_storage.redis.ping)
        except RedisError as exc:
            raise RuntimeError("FlowStateManager requires Redis") from exc

    def generate_flow_id(self, user_id: str, server_id: str) -> str:
        """
        Generate OAuth flow ID
        """
        return f"{user_id}:{server_id}"

    @classmethod
    def encode_state(
        cls, flow_id: str, security_token: str | None = None, *, state_metadata: StateMetadata | None = None
    ) -> str:
        """
        Encode state parameter with CSRF protection
        """
        if security_token is None:
            security_token = secrets.token_urlsafe(32)

        state_dict: OAuthFlowState = {"flow_id": flow_id, "security_token": security_token}

        # If state_metadata is not None, it's a dictionary passed all the way down from mcpgw's tool call handler function.
        # The execution flow reaching here from a tool call handler function means we need a URL mode elicitation,
        # so we generate an UUID for it.
        if state_metadata is not None:
            state_dict["meta"] = state_metadata.copy()
            state_dict["meta"]["elicitation_id"] = str(uuid4())

        state = base64.urlsafe_b64encode(json.dumps(state_dict).encode("utf-8")).decode("utf-8").rstrip("=")

        logger.debug(f"Encoded state: flow_id={flow_id}, token_length={len(security_token)}")

        return state

    @classmethod
    def decode_state(cls, state: str) -> OAuthFlowState:
        """Decode state parameter"""

        state += "=" * ((-len(state)) % 4)
        try:
            state_dict = json.loads(base64.urlsafe_b64decode(state))
        except Exception:
            error_msg = "state is not valid base64url encoded JSON string."
            logger.exception(error_msg)

            raise ValueError(error_msg)

        if not isinstance(state_dict, dict):
            error_msg = "state is valid base64url encoded JSON, but not a JSON object."
            logger.error(error_msg)

            raise ValueError(error_msg)

        if "flow_id" not in state_dict or not isinstance(state_dict["flow_id"], str):
            error_msg = "The flow_id key is not in decoded state dictionary."
            logger.error(error_msg)

            raise ValueError(error_msg)

        if "security_token" not in state_dict or not isinstance(state_dict["security_token"], str):
            error_msg = "The security_token key is not in decoded state dictionary."
            logger.error(error_msg)

            raise ValueError(error_msg)

        logger.debug(
            f"Decoded state: flow_id={state_dict['flow_id']}, token_length={len(state_dict['security_token'])}"
        )

        return cast(OAuthFlowState, state_dict)

    def create_flow_metadata(
        self,
        server_name: str,
        server_path: str,
        server_id: str,
        user_id: str,
        authorization_url: str,
        code_verifier: str,
        oauth_config: dict[str, Any],
        flow_id: str,
        *,
        redirect_uri: str,
        state_metadata: StateMetadata | None = None,
        mcp_client_context: MCPClientContext | None = None,
        device_code: str | None = None,
    ) -> MCPOAuthFlowMetadata:
        """Create OAuth flow metadata"""
        # Generate secure state parameter (base64url encoded JSON string)
        security_token = secrets.token_urlsafe(32)
        state = self.encode_state(flow_id, security_token, state_metadata=state_metadata)

        server_path = server_path.strip()

        resource_url = oauth_config.get("resource") or (oauth_config.get("additional_params") or {}).get("resource")
        resource_metadata = OAuthProtectedResourceMetadata(resource=resource_url) if resource_url else None

        return MCPOAuthFlowMetadata(  # type: ignore [call-arg]
            server_id=server_id.strip(),
            server_name=server_name.strip(),
            server_path=server_path,
            user_id=user_id.strip(),
            authorization_url=authorization_url,
            state=state,
            code_verifier=code_verifier,
            client_info=self._create_client_info(
                oauth_config,
                redirect_uri=redirect_uri,
            ),
            metadata=self._create_oauth_metadata(oauth_config),
            resource_metadata=resource_metadata,
            mcp_client_context=mcp_client_context,
            device_code=device_code,
        )

    async def create_flow(
        self, flow_id: str, server_id: str, user_id: str, code_verifier: str, metadata: MCPOAuthFlowMetadata
    ) -> OAuthFlow:
        flow = OAuthFlow(
            flow_id=flow_id,
            server_id=server_id,
            server_name=metadata.server_name,
            user_id=user_id,
            code_verifier=code_verifier,
            state=metadata.state,
            status=OAuthFlowStatus.PENDING,
            created_at=time.time(),
            metadata=metadata,
        )
        await self._run_storage(self._redis_storage.save_flow, flow, self._flow_ttl)
        return flow

    async def get_flow(self, flow_id: str) -> OAuthFlow | None:
        return await self._run_storage(self._redis_storage.get_flow, flow_id)

    async def get_flows(self, flow_ids: list[str]) -> dict[str, OAuthFlow | OAuthFlowStorageError | None]:
        if not flow_ids:
            return {}
        return await self._run_storage(self._redis_storage.get_flows, flow_ids)

    def is_flow_expired(self, flow: OAuthFlow) -> bool:
        return not flow.created_at or time.time() - flow.created_at >= self._flow_ttl

    async def consume_flow(self, flow_id: str, expected_state: str) -> OAuthFlow | None:
        return await self._run_storage(self._redis_storage.consume_flow, flow_id, expected_state)

    async def delete_flow(self, flow_id: str) -> None:
        await self._run_storage(self._redis_storage.delete_flow, flow_id)

    async def complete_flow(self, flow_id: str, tokens: OAuthTokens) -> None:
        flow = await self.get_flow(flow_id)
        if flow is None:
            raise ValueError("OAuth flow not found")
        flow.status = OAuthFlowStatus.COMPLETED
        flow.completed_at = time.time()
        flow.tokens = tokens
        await self._run_storage(self._redis_storage.save_flow, flow, self._flow_ttl)

    async def fail_flow(self, flow_id: str, error: str) -> None:
        flow = await self.get_flow(flow_id)
        if flow is None:
            raise ValueError("OAuth flow not found")
        flow.status = OAuthFlowStatus.FAILED
        flow.error = error
        await self._run_storage(self._redis_storage.save_flow, flow, self._flow_ttl)

    async def cancel_user_flow(self, user_id: str, server_id: str) -> bool:
        flow_id = self.generate_flow_id(user_id, server_id)
        flow = await self.get_flow(flow_id)
        if flow is None or flow.status != OAuthFlowStatus.PENDING:
            return False
        await self.fail_flow(flow_id, "User cancelled OAuth flow")
        return True

    async def get_user_flows(self, user_id: str, server_id: str) -> list[OAuthFlow]:
        flow = await self.get_flow(self.generate_flow_id(user_id, server_id))
        return [flow] if flow else []

    def _create_client_info(
        self,
        oauth_config: dict[str, Any],
        *,
        redirect_uri: str,
    ) -> OAuthClientInformation:
        """
        Build OAuth client information from server configuration
        """
        scope_string = scope_to_string(oauth_config.get("scope"))
        logger.debug(f"Client info - redirect_uris: [{redirect_uri}], scopes: {scope_string}")
        return OAuthClientInformation(  # type: ignore [call-arg]
            client_id=str(oauth_config.get("client_id", "")).strip(),
            client_secret=str(oauth_config.get("client_secret")).strip() if oauth_config.get("client_secret") else None,
            redirect_uris=[redirect_uri],
            scope=scope_string,
            additional_params=oauth_config.get("additional_params"),
        )

    def _create_oauth_metadata(self, oauth_config: dict[str, Any]) -> OAuthMetadata:
        """
        Build OAuth metadata from server configuration
        """
        auth_url = oauth_config.get("authorization_url", "")
        token_url = oauth_config.get("token_url", "")

        scopes = parse_scope(oauth_config.get("scope"), default=[])

        return OAuthMetadata(  # type: ignore [call-arg]
            authorization_endpoint=auth_url,
            token_endpoint=token_url,
            issuer=oauth_config.get("issuer", ""),
            scopes_supported=scopes,
            grant_types_supported=oauth_config.get("grant_types_supported", ["authorization_code", "refresh_token"]),
            token_endpoint_auth_methods_supported=oauth_config.get(
                "token_endpoint_auth_methods_supported", ["client_secret_post", "client_secret_basic"]
            ),
            response_types_supported=oauth_config.get("response_types_supported", ["code"]),
            code_challenge_methods_supported=oauth_config.get("code_challenge_methods_supported", ["S256", "plain"]),
        )

    async def cleanup_expired_flows(self) -> int:
        return await self._run_storage(self._redis_storage.cleanup_expired, self._flow_ttl)
