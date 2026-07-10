"""Redis-backed consent state shared by auth-server and registry."""

import json
import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .oauth_state_store import CLIENT_TTL_SECONDS

logger = logging.getLogger(__name__)

CLIENT_CONSENT_FIELD = "jarvis-registry-client-id-consent"
PENDING_CONSENT_TTL_SECONDS = 600


class ConsentStore:
    """Per-(user_id, client_id) consent hash shared across auth-server and registry."""

    def __init__(self, redis_client: Redis, key_prefix: str) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix

    def _key(
        self,
        user_id: str,
        client_id: str,
    ) -> str:
        return f"{self._key_prefix}:mcp:consent:{user_id}:{client_id}"

    def has_client_consent(
        self,
        user_id: str,
        client_id: str,
    ) -> bool:
        key = self._key(user_id, client_id)
        return self._has_consent_field(key, CLIENT_CONSENT_FIELD, user_id=user_id, client_id=client_id)

    def grant_client_consent(
        self,
        user_id: str,
        client_id: str,
    ) -> None:
        key = self._key(user_id, client_id)
        self._grant_consent_field(key, CLIENT_CONSENT_FIELD, user_id=user_id, client_id=client_id)

    def has_server_consent(
        self,
        user_id: str,
        client_id: str,
        server_path: str,
    ) -> bool:
        key = self._key(user_id, client_id)
        return self._has_consent_field(
            key,
            server_path,
            user_id=user_id,
            client_id=client_id,
            server_path=server_path,
        )

    def grant_server_consent(
        self,
        user_id: str,
        client_id: str,
        server_path: str,
    ) -> None:
        key = self._key(user_id, client_id)
        self._grant_consent_field(
            key,
            server_path,
            user_id=user_id,
            client_id=client_id,
            server_path=server_path,
        )

    def _has_consent_field(
        self,
        key: str,
        field: str,
        *,
        user_id: str,
        client_id: str,
        server_path: str | None = None,
    ) -> bool:
        try:
            if not self._redis.hexists(key, field):
                return False
            self._redis.expire(key, CLIENT_TTL_SECONDS)
            return True
        except RedisError:
            logger.exception(
                "Failed to check consent user_id=%s client_id=%s server_path=%s",
                user_id,
                client_id,
                server_path,
            )
            raise

    def _grant_consent_field(
        self,
        key: str,
        field: str,
        *,
        user_id: str,
        client_id: str,
        server_path: str | None = None,
    ) -> None:
        try:
            self._redis.hset(key, field, "1")
            self._redis.expire(key, CLIENT_TTL_SECONDS)
        except RedisError:
            logger.exception(
                "Failed to grant consent user_id=%s client_id=%s server_path=%s",
                user_id,
                client_id,
                server_path,
            )
            raise


class PendingConsentStore:
    """One-shot, short-lived storage for consent detour state."""

    def __init__(self, redis_client: Redis, key_prefix: str) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix

    def _key(self, nonce: str) -> str:
        return f"{self._key_prefix}:mcp:consent-pending:{nonce}"

    def save(
        self,
        nonce: str,
        data: dict[str, Any],
        ttl_seconds: int = PENDING_CONSENT_TTL_SECONDS,
    ) -> None:
        try:
            self._redis.set(self._key(nonce), json.dumps(data, separators=(",", ":")), ex=ttl_seconds)
        except RedisError:
            logger.exception("Failed to save pending consent context nonce=%s", nonce)
            raise

    def peek(self, nonce: str) -> dict[str, Any] | None:
        try:
            raw = self._redis.get(self._key(nonce))
        except RedisError:
            logger.exception("Failed to read pending consent context nonce=%s", nonce)
            raise
        if raw is None:
            return None
        return json.loads(raw)

    def consume(self, nonce: str) -> dict[str, Any] | None:
        try:
            raw = self._redis.getdel(self._key(nonce))
        except RedisError:
            logger.exception("Failed to consume pending consent context nonce=%s", nonce)
            raise
        if raw is None:
            return None
        return json.loads(raw)
