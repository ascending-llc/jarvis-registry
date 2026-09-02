import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from beanie import PydanticObjectId

from registry.utils.crypto_utils import decrypt_value, encrypt_value
from registry_pkgs.models import Token
from registry_pkgs.models.token_type import TokenType
from registry_pkgs.oauth.schemas import OAuthTokens

logger = logging.getLogger(__name__)

_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
# GitHub Apps without "user-to-server token expiration" return no expires_in;
# these large defaults effectively mean "never expire on our side".
_DEFAULT_ACCESS_TOKEN_LIFETIME = timedelta(days=3650)
_DEFAULT_REFRESH_TOKEN_LIFETIME = timedelta(days=365)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _is_unexpired(expires_at: datetime, now: datetime) -> bool:
    return _as_utc_naive(expires_at) > now


def build_skill_sync_token_identifier(source_id: str | PydanticObjectId) -> str:
    return f"skillsync:{source_id}"


class SkillSyncTokenService:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def resolve_access_token(
        self,
        *,
        user_id: str,
        source_id: str | PydanticObjectId,
        client_id: str,
        client_secret: str,
    ) -> str | None:
        """Try access token → refresh fallback → None (caller must re-authorize)."""
        user_object_id = PydanticObjectId(user_id)
        identifier = build_skill_sync_token_identifier(source_id)
        access = await Token.find_one(
            {
                "userId": user_object_id,
                "type": TokenType.SKILL_SYNC_GITHUB_ACCESS.value,
                "identifier": identifier,
            }
        )
        now = _utc_now()
        if access is not None and _is_unexpired(access.expiresAt, now):
            return decrypt_value(access.token)

        refresh = await Token.find_one(
            {
                "userId": user_object_id,
                "type": TokenType.SKILL_SYNC_GITHUB_REFRESH.value,
                "identifier": identifier,
                "expiresAt": {"$gt": now},
            }
        )
        if refresh is None:
            return None
        try:
            tokens = await self.refresh_tokens(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=decrypt_value(refresh.token),
            )
        except (httpx.HTTPError, ValueError):
            logger.exception("Failed to refresh GitHub token for skill sync source %s", source_id)
            return None
        await self.store_tokens(user_id=user_id, source_id=source_id, tokens=tokens)
        return tokens.access_token

    async def refresh_tokens(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> OAuthTokens:
        response = await self._http_client.post(
            _GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error") or not payload.get("access_token"):
            raise ValueError(payload.get("error_description") or "GitHub token refresh failed")
        return OAuthTokens(**payload)

    async def store_tokens(
        self,
        *,
        user_id: str,
        source_id: str | PydanticObjectId,
        tokens: OAuthTokens,
    ) -> None:
        identifier = build_skill_sync_token_identifier(source_id)
        user_object_id = PydanticObjectId(user_id)
        coros = []
        if tokens.access_token:
            coros.append(
                self._upsert_token(
                    user_id=user_object_id,
                    token_type=TokenType.SKILL_SYNC_GITHUB_ACCESS,
                    identifier=identifier,
                    value=tokens.access_token,
                    expires_in=tokens.expires_in,
                    default_lifetime=_DEFAULT_ACCESS_TOKEN_LIFETIME,
                )
            )
        if tokens.refresh_token:
            coros.append(
                self._upsert_token(
                    user_id=user_object_id,
                    token_type=TokenType.SKILL_SYNC_GITHUB_REFRESH,
                    identifier=identifier,
                    value=tokens.refresh_token,
                    expires_in=None,
                    default_lifetime=_DEFAULT_REFRESH_TOKEN_LIFETIME,
                )
            )
        if coros:
            await asyncio.gather(*coros)

    async def _upsert_token(
        self,
        *,
        user_id: PydanticObjectId,
        token_type: TokenType,
        identifier: str,
        value: str,
        expires_in: int | None,
        default_lifetime: timedelta,
    ) -> None:
        expires_at = _utc_now() + (timedelta(seconds=expires_in) if expires_in else default_lifetime)
        query: dict[str, Any] = {"userId": user_id, "type": token_type.value, "identifier": identifier}
        token = await Token.find_one(query)
        if token is None:
            await Token(
                userId=user_id,
                type=token_type.value,
                identifier=identifier,
                token=encrypt_value(value),
                expiresAt=expires_at,
            ).insert()
            return
        token.token = encrypt_value(value)
        token.expiresAt = expires_at
        await token.save()

    async def delete_user_access_token(self, *, user_id: str, source_id: str | PydanticObjectId) -> None:
        await Token.find(
            {
                "userId": PydanticObjectId(user_id),
                "type": TokenType.SKILL_SYNC_GITHUB_ACCESS.value,
                "identifier": build_skill_sync_token_identifier(source_id),
            }
        ).delete()

    async def delete_source_tokens(self, source_id: str | PydanticObjectId) -> None:
        await Token.find(
            {
                "identifier": build_skill_sync_token_identifier(source_id),
                "type": {
                    "$in": [
                        TokenType.SKILL_SYNC_GITHUB_ACCESS.value,
                        TokenType.SKILL_SYNC_GITHUB_REFRESH.value,
                    ]
                },
            }
        ).delete()
