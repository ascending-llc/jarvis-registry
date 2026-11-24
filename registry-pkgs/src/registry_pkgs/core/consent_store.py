"""Redis-backed consent state shared by auth-server and registry."""

import json
import logging
import secrets
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Any, Self

from bson import ObjectId
from redis import Redis
from redis.exceptions import RedisError

from .oauth_state_store import CLIENT_TTL_SECONDS

logger = logging.getLogger(__name__)

CLIENT_CONSENT_FIELD = "jarvis-registry-client-id-consent"
LEGACY_CONSENT_GRANT_ID = "1"
PENDING_CONSENT_TTL_SECONDS = 600
_GRANT_ID_BYTES = 16
_AGENT_FIELD_PREFIX = "agent:"
_SCAN_COUNT = 1000

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

_GRANT_CONSENT_FIELD_SCRIPT = """
if ARGV[1] == '1' and redis.call('HEXISTS', KEYS[1], ARGV[2]) == 0 then return 0 end
redis.call('HSET', KEYS[1], ARGV[3], '1')
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""


def effective_consent_grant_id(presented_grant_id: str | None) -> str:
    """Unstamped credentials belong to the legacy grant."""
    return LEGACY_CONSENT_GRANT_ID if presented_grant_id is None else presented_grant_id


def consent_grant_matches(stored_grant_id: str | None, presented_grant_id: str | None) -> bool:
    """A missing consent never matches; unstamped credentials match only the legacy grant."""
    return stored_grant_id is not None and stored_grant_id == effective_consent_grant_id(presented_grant_id)


class ConsentEntryKind(StrEnum):
    APP = "app"
    MCP_SERVER = "mcp_server"
    A2A_AGENT = "a2a_agent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConsentEntry:
    kind: ConsentEntryKind
    path: str

    @classmethod
    def from_field(cls, field: str) -> Self:
        if field == CLIENT_CONSENT_FIELD:
            return cls(ConsentEntryKind.APP, "")
        if field.startswith(_AGENT_FIELD_PREFIX):
            return cls(ConsentEntryKind.A2A_AGENT, field.removeprefix(_AGENT_FIELD_PREFIX))
        if field.startswith("/"):
            return cls(ConsentEntryKind.MCP_SERVER, field)
        return cls(ConsentEntryKind.UNKNOWN, field)

    def to_field(self) -> str:
        if self.kind == ConsentEntryKind.APP:
            return CLIENT_CONSENT_FIELD
        if self.kind == ConsentEntryKind.A2A_AGENT:
            return f"{_AGENT_FIELD_PREFIX}{self.path}"
        if self.kind == ConsentEntryKind.MCP_SERVER:
            if self.path == CLIENT_CONSENT_FIELD or self.path.startswith(_AGENT_FIELD_PREFIX):
                raise ValueError("Server path collides with a reserved consent field")
            return self.path
        raise ValueError("Cannot encode an unknown consent field")


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

    def list_client_ids(self, user_id: str) -> list[str]:
        """Scan this user's consent hashes, counting every cursor reply, including empty pages."""
        if not ObjectId.is_valid(user_id):
            raise ValueError("Invalid user ID")
        prefix = self._key(user_id, "")
        started = perf_counter()
        client_ids: set[str] = set()
        cursor = rounds = 0
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=f"{prefix}*", count=_SCAN_COUNT)
            rounds += 1
            client_ids.update(key.removeprefix(prefix) for key in keys)
            if cursor == 0:
                break
        logger.info(
            "Scanned consent hashes user_id=%s keys_found=%d scan_round_trips=%d elapsed_ms=%.1f",
            user_id,
            len(client_ids),
            rounds,
            (perf_counter() - started) * 1000,
        )
        return sorted(client_ids)

    def get_entries(self, user_id: str, client_ids: list[str]) -> dict[str, list[ConsentEntry]]:
        """Read surviving consent hashes without renewing them or returning grant IDs."""
        if not client_ids:
            return {}
        with self._redis.pipeline(transaction=False) as pipe:
            for client_id in client_ids:
                pipe.hgetall(self._key(user_id, client_id))
            hashes = pipe.execute()
        return {
            client_id: [ConsentEntry.from_field(field) for field in fields]
            for client_id, fields in zip(client_ids, hashes, strict=True)
            if fields
        }

    def delete_clients(self, user_id: str, client_ids: list[str]) -> dict[str, bool]:
        """Delete the user's consent hashes and report which clients had stored consent."""
        if not ObjectId.is_valid(user_id):
            raise ValueError("Invalid user ID")
        unique_ids = dict.fromkeys(client_ids)
        with self._redis.pipeline(transaction=False) as pipe:
            for client_id in unique_ids:
                pipe.delete(self._key(user_id, client_id))
            return dict(zip(unique_ids, map(bool, pipe.execute()), strict=True))

    def delete_entries(self, user_id: str, client_id: str, entries: list[ConsentEntry]) -> dict[ConsentEntry, bool]:
        """Delete individual resource approvals without renewing the remaining consent."""
        if any(entry.kind == ConsentEntryKind.APP for entry in entries):
            raise ValueError("Use whole-app revocation to remove app consent")
        fields = {entry: entry.to_field() for entry in entries}
        key = self._key(user_id, client_id)
        with self._redis.pipeline(transaction=False) as pipe:
            for field in fields.values():
                pipe.hdel(key, field)
            return dict(zip(fields, map(bool, pipe.execute()), strict=True))

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
            ConsentEntry(ConsentEntryKind.MCP_SERVER, server_path).to_field(),
            user_id=user_id,
            client_id=client_id,
            server_path=server_path,
        )

    def grant_server_consent(
        self,
        user_id: str,
        client_id: str,
        server_path: str,
        *,
        require_app_consent: bool,
    ) -> bool:
        key = self._key(user_id, client_id)
        return self._grant_consent_field(
            key,
            ConsentEntry(ConsentEntryKind.MCP_SERVER, server_path).to_field(),
            require_app_consent=require_app_consent,
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
            ConsentEntry(ConsentEntryKind.A2A_AGENT, agent_path).to_field(),
            user_id=user_id,
            client_id=client_id,
            server_path=agent_path,
        )

    def grant_agent_consent(
        self,
        user_id: str,
        client_id: str,
        agent_path: str,
        *,
        require_app_consent: bool,
    ) -> bool:
        key = self._key(user_id, client_id)
        return self._grant_consent_field(
            key,
            ConsentEntry(ConsentEntryKind.A2A_AGENT, agent_path).to_field(),
            require_app_consent=require_app_consent,
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
        require_app_consent: bool,
        user_id: str,
        client_id: str,
        server_path: str | None = None,
    ) -> bool:
        try:
            return bool(
                self._redis.eval(
                    _GRANT_CONSENT_FIELD_SCRIPT,
                    1,
                    key,
                    int(require_app_consent),
                    CLIENT_CONSENT_FIELD,
                    field,
                    CLIENT_TTL_SECONDS,
                )
            )
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
