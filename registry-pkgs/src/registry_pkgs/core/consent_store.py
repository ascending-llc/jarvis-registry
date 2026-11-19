"""Redis-backed consent state shared by auth-server and registry."""

import json
import logging
import secrets
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .oauth_state_store import CLIENT_TTL_SECONDS

logger = logging.getLogger(__name__)

CLIENT_CONSENT_FIELD = "jarvis-registry-client-id-consent"
LEGACY_CONSENT_GRANT_ID = "1"
PENDING_CONSENT_TTL_SECONDS = 600
_GRANT_ID_BYTES = 16

_GET_CLIENT_GRANT_SCRIPT = """
local value = redis.call('HGET', KEYS[1], ARGV[1])
if value then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return value
"""

_HAS_CONSENT_FIELD_SCRIPT = """
local exists = redis.call('HEXISTS', KEYS[1], ARGV[1])
if exists == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return exists
"""


def effective_consent_grant_id(presented_grant_id: str | None) -> str:
    """Unstamped credentials belong to the legacy grant."""
    return LEGACY_CONSENT_GRANT_ID if presented_grant_id is None else presented_grant_id


def consent_grant_matches(stored_grant_id: str | None, presented_grant_id: str | None) -> bool:
    """A missing consent never matches; unstamped credentials match only the legacy grant."""
    return stored_grant_id is not None and stored_grant_id == effective_consent_grant_id(presented_grant_id)


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

    def get_client_grant_id(
        self,
        user_id: str,
        client_id: str,
    ) -> str | None:
        key = self._key(user_id, client_id)
        try:
            return self._redis.eval(_GET_CLIENT_GRANT_SCRIPT, 1, key, CLIENT_CONSENT_FIELD, CLIENT_TTL_SECONDS)
        except RedisError:
            logger.exception("Failed to get consent grant user_id=%s client_id=%s", user_id, client_id)
            raise

    def grant_client_consent(
        self,
        user_id: str,
        client_id: str,
    ) -> str:
        """Return the existing grant, or create one without replacing a concurrent approval."""
        key = self._key(user_id, client_id)
        try:
            with self._redis.pipeline(transaction=True) as pipe:
                pipe.hsetnx(key, CLIENT_CONSENT_FIELD, secrets.token_urlsafe(_GRANT_ID_BYTES))
                pipe.hget(key, CLIENT_CONSENT_FIELD)
                pipe.expire(key, CLIENT_TTL_SECONDS)
                return pipe.execute()[1]
        except RedisError:
            logger.exception("Failed to grant client consent user_id=%s client_id=%s", user_id, client_id)
            raise

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

    def has_agent_consent(
        self,
        user_id: str,
        client_id: str,
        agent_path: str,
    ) -> bool:
        key = self._key(user_id, client_id)
        return self._has_consent_field(
            key,
            f"agent:{agent_path}",
            user_id=user_id,
            client_id=client_id,
            server_path=agent_path,
        )

    def grant_agent_consent(
        self,
        user_id: str,
        client_id: str,
        agent_path: str,
    ) -> None:
        key = self._key(user_id, client_id)
        self._grant_consent_field(
            key,
            f"agent:{agent_path}",
            user_id=user_id,
            client_id=client_id,
            server_path=agent_path,
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
            return bool(self._redis.eval(_HAS_CONSENT_FIELD_SCRIPT, 1, key, field, CLIENT_TTL_SECONDS))
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
