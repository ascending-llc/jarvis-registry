import asyncio
import hashlib
import logging
import secrets
from urllib.parse import urlencode

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from redis import Redis
from redis.exceptions import RedisError

from registry.utils.crypto_utils import decrypt_value
from registry_pkgs.models.skill_sync_source import SkillSyncSource
from registry_pkgs.oauth.flow_state_manager import FlowStateManager
from registry_pkgs.oauth.schemas import OAuthTokens

from .skill_sync_token_service import SkillSyncTokenService

logger = logging.getLogger(__name__)

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_COMPLETED_FLOW_MARKER_KEY = "{prefix}:skill_sync:oauth_completed:{flow_id}"
_COMPLETED_FLOW_MARKER_TTL_SECONDS = 86400


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


class SkillSyncOAuthService:
    def __init__(
        self,
        *,
        flow_state_manager: FlowStateManager,
        token_service: SkillSyncTokenService,
        http_client: httpx.AsyncClient,
        redis_client: Redis,
        redis_key_prefix: str,
    ) -> None:
        self._flow_state_manager = flow_state_manager
        self._token_service = token_service
        self._http_client = http_client
        self._redis_client = redis_client
        self._redis_key_prefix = redis_key_prefix

    async def create_authorization_url(
        self,
        *,
        source: SkillSyncSource,
        user_id: str,
        redirect_uri: str,
    ) -> str:
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = create_s256_code_challenge(code_verifier)
        flow_id = f"skillsync:{user_id}:{source.id}:{secrets.token_urlsafe(8)}"
        oauth_config = {
            "client_id": source.githubAppClientId,
            "authorization_url": _GITHUB_AUTHORIZE_URL,
            "token_url": _GITHUB_TOKEN_URL,
            "scope": "",
        }
        metadata = self._flow_state_manager.create_flow_metadata(
            server_name="github-skill-sync",
            server_path=str(source.id),
            server_id=str(source.id),
            user_id=user_id,
            authorization_url=_GITHUB_AUTHORIZE_URL,
            code_verifier=code_verifier,
            oauth_config=oauth_config,
            flow_id=flow_id,
            redirect_uri=redirect_uri,
        )
        params = urlencode(
            {
                "client_id": source.githubAppClientId,
                "redirect_uri": redirect_uri,
                "state": metadata.state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        authorization_url = f"{_GITHUB_AUTHORIZE_URL}?{params}"
        metadata.authorization_url = authorization_url
        await self._flow_state_manager.create_flow(flow_id, str(source.id), user_id, code_verifier, metadata)
        return authorization_url

    async def resolve_source_id(self, state: str) -> str:
        decoded = self._flow_state_manager.decode_state(state)
        flow = await self._flow_state_manager.get_flow(decoded["flow_id"])
        if flow is None or not secrets.compare_digest(flow.state, state):
            raise ValueError("OAuth state does not reference a known flow")
        return flow.server_id

    def _completed_marker_key(self, flow_id: str) -> str:
        return _COMPLETED_FLOW_MARKER_KEY.format(prefix=self._redis_key_prefix, flow_id=flow_id)

    async def resolve_completed_source_id(self, state: str) -> str | None:
        try:
            decoded = self._flow_state_manager.decode_state(state)
        except ValueError:
            return None

        key = self._completed_marker_key(decoded["flow_id"])
        try:
            marker = await asyncio.to_thread(self._redis_client.get, key)
        except RedisError:
            logger.warning("Failed to read GitHub OAuth completed-flow marker")
            return None

        if not marker:
            return None
        stored_hash, separator, source_id = marker.partition(":")
        if separator and source_id and secrets.compare_digest(stored_hash, _hash_state(state)):
            return source_id
        return None

    async def exchange_callback(
        self,
        *,
        source: SkillSyncSource,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> str:
        decoded = self._flow_state_manager.decode_state(state)
        flow = await self._flow_state_manager.consume_flow(decoded["flow_id"], state)
        if flow is None:
            raise ValueError("OAuth state is invalid or expired")
        response = await self._http_client.post(
            _GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": source.githubAppClientId,
                "client_secret": decrypt_value(source.githubAppClientSecretEncrypted),
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": flow.code_verifier,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error") or not payload.get("access_token"):
            raise ValueError(payload.get("error_description") or "GitHub OAuth token exchange failed")
        tokens = OAuthTokens(**payload)
        await self._token_service.store_tokens(user_id=flow.user_id, source_id=source.id, tokens=tokens)

        key = self._completed_marker_key(decoded["flow_id"])
        # Hash the state: it carries the flow's security token.
        marker = f"{_hash_state(state)}:{flow.server_id}"
        try:
            await asyncio.to_thread(self._redis_client.set, key, marker, ex=_COMPLETED_FLOW_MARKER_TTL_SECONDS)
        except RedisError:
            # Tokens are already stored; the marker only lets a later replay be recognized, so don't fail the callback.
            logger.warning("Failed to write GitHub OAuth completed-flow marker")
        return flow.user_id
