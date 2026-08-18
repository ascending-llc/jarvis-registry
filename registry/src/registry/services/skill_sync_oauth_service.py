import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from registry.auth.oauth.flow_state_manager import FlowStateManager
from registry.schemas.oauth_schema import OAuthTokens
from registry.utils.crypto_utils import decrypt_value
from registry_pkgs.models.skill_sync_source import SkillSyncSource

from .skill_sync_token_service import SkillSyncTokenService

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"


class SkillSyncOAuthService:
    def __init__(
        self,
        *,
        flow_state_manager: FlowStateManager,
        token_service: SkillSyncTokenService,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._flow_state_manager = flow_state_manager
        self._token_service = token_service
        self._http_client = http_client

    def create_authorization_url(
        self,
        *,
        source: SkillSyncSource,
        user_id: str,
        redirect_uri: str,
    ) -> str:
        code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        flow_id = f"skillsync:{user_id}:{source.id}:{secrets.token_urlsafe(8)}"
        oauth_config = {
            "client_id": source.githubAppClientId,
            "client_secret": decrypt_value(source.githubAppClientSecretEncrypted),
            "redirect_uri": redirect_uri,
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
        )
        authorization_url = f"{_GITHUB_AUTHORIZE_URL}?{
            urlencode(
                {
                    'client_id': source.githubAppClientId,
                    'redirect_uri': redirect_uri,
                    'state': metadata.state,
                    'code_challenge': code_challenge,
                    'code_challenge_method': 'S256',
                }
            )
        }"
        metadata.authorization_url = authorization_url
        self._flow_state_manager.create_flow(flow_id, str(source.id), user_id, code_verifier, metadata)
        return authorization_url

    async def exchange_callback(
        self,
        *,
        source: SkillSyncSource,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> str:
        decoded = self._flow_state_manager.decode_state(state)
        flow = self._flow_state_manager.consume_flow(decoded["flow_id"], state)
        if flow is None or flow.server_id != str(source.id):
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
        return flow.user_id
