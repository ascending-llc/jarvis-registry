"""Unit tests for Redis-backed consent stores."""

import json
from typing import Any

from registry_pkgs.core.consent_store import (
    CLIENT_CONSENT_FIELD,
    PENDING_CONSENT_TTL_SECONDS,
    ConsentStore,
    PendingConsentStore,
)
from registry_pkgs.core.oauth_state_store import CLIENT_TTL_SECONDS


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}

    def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def hexists(self, key: str, field: str) -> bool:
        return field in self.hashes.get(key, {})

    def expire(self, key: str, ttl_seconds: int) -> bool:
        self.ttls[key] = ttl_seconds
        return True

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


def test_client_consent_uses_shared_hash_and_slides_ttl() -> None:
    redis = _FakeRedis()
    store = ConsentStore(redis_client=redis, key_prefix="jarvis-auth-server-test")

    assert store.has_client_consent("user-1", "client-1") is False

    store.grant_client_consent("user-1", "client-1")

    key = "jarvis-auth-server-test:mcp:consent:user-1:client-1"
    assert redis.hashes[key][CLIENT_CONSENT_FIELD] == "1"
    assert redis.ttls[key] == CLIENT_TTL_SECONDS
    redis.ttls[key] = 100

    assert store.has_client_consent("user-1", "client-1") is True
    assert redis.ttls[key] == CLIENT_TTL_SECONDS


def test_server_consent_shares_client_hash() -> None:
    redis = _FakeRedis()
    store = ConsentStore(redis_client=redis, key_prefix="jarvis-auth-server-test")

    assert store.has_server_consent("user-1", "client-1", "/github") is False

    store.grant_server_consent("user-1", "client-1", "/github")

    key = "jarvis-auth-server-test:mcp:consent:user-1:client-1"
    assert redis.hashes[key]["/github"] == "1"
    assert store.has_server_consent("user-1", "client-1", "/github") is True


def test_pending_consent_peek_and_consume_are_one_shot() -> None:
    redis = _FakeRedis()
    store = PendingConsentStore(redis_client=redis, key_prefix="jarvis-auth-server-test")
    payload: dict[str, Any] = {"user_id": "user-1", "client_id": "client-1"}

    store.save("nonce-1", payload)

    key = "jarvis-auth-server-test:mcp:consent-pending:nonce-1"
    assert json.loads(redis.values[key]) == payload
    assert redis.ttls[key] == PENDING_CONSENT_TTL_SECONDS
    assert store.peek("nonce-1") == payload
    assert store.consume("nonce-1") == payload
    assert store.consume("nonce-1") is None
