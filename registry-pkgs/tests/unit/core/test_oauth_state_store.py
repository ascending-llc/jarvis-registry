"""Unit tests for Redis-backed OAuth state storage."""

import fnmatch
import json
from typing import Any

import pytest

from registry_pkgs.core.oauth_state_store import (
    AUTH_CODE_TTL_SECONDS,
    CLIENT_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    DownstreamOAuthStateStore,
    OAuthClientStore,
    OAuthStateStore,
)


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self.commands: list[tuple[str, str, int | None]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> "_FakePipeline":
        self.commands.append((key, value, ex))
        return self

    def execute(self) -> list[bool]:
        for key, value, ex in self.commands:
            self._redis.set(key, value, ex=ex)
        return [True for _ in self.commands]


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.last_pipeline_transaction: bool | None = None

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        self.ttls[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl_seconds: int) -> bool:
        if key not in self.values:
            return False
        self.ttls[key] = ttl_seconds
        return True

    def ttl(self, key: str) -> int:
        if key not in self.values:
            return -2
        ttl = self.ttls.get(key)
        if ttl is None:
            return -1
        return ttl

    def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return 1 if existed else 0

    def scan_iter(self, match: str, count: int = 100) -> list[str]:
        return [key for key in self.values if fnmatch.fnmatch(key, match)]

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        self.last_pipeline_transaction = transaction
        return _FakePipeline(self)

    def eval(self, script: str, numkeys: int, *args: Any) -> str | None:
        if numkeys == 2:
            if len(args) == 7:
                return self._issue_device_refresh_token(args)
            return self._rotate_refresh_token(args)
        return self._consume_authcode(args)

    def _consume_authcode(self, args: tuple[Any, ...]) -> str | None:
        key = str(args[0])
        value = self.get(key)
        if value is None:
            return None
        self.delete(key)
        return value

    def _rotate_refresh_token(self, args: tuple[Any, ...]) -> str | None:
        old_key = str(args[0])
        new_key = str(args[1])
        ttl_seconds = int(args[2])
        new_value = str(args[3])

        old_value = self.get(old_key)
        if old_value is None:
            return None

        self.delete(old_key)
        self.set(new_key, new_value, ex=ttl_seconds)
        return old_value

    def _issue_device_refresh_token(self, args: tuple[Any, ...]) -> str | None:
        device_key = str(args[0])
        refresh_key = str(args[1])
        expected_client_id = str(args[2])
        expected_user_id = str(args[3])
        expected_server_path = str(args[4])
        ttl_seconds = int(args[5])
        refresh_data = str(args[6])

        raw_device = self.get(device_key)
        if raw_device is None:
            return None
        device_data = json.loads(raw_device)
        if (
            device_data.get("status") != "approved"
            or device_data.get("client_id") != expected_client_id
            or device_data.get("user_id") != expected_user_id
            or device_data.get("server_path") != expected_server_path
        ):
            return None

        self.set(refresh_key, refresh_data, ex=ttl_seconds)
        self.delete(device_key)
        return raw_device


@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def store(fake_redis: _FakeRedis) -> OAuthStateStore:
    return OAuthStateStore(
        redis_client=fake_redis,
        key_prefix="jarvis-auth-server-test",
        client_secret_hash_key="hash-key",
    )


def test_save_and_get_client_hashes_secret_and_slides_ttl(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_client(
        "client-1",
        {
            "client_id": "client-1",
            "client_secret": "secret-value",
            "token_endpoint_auth_method": "client_secret_post",
            "client_name": "Test Client",
        },
    )

    key = "jarvis-auth-server-test:oauth:client:client-1"
    stored = json.loads(fake_redis.values[key])

    assert stored["client_id"] == "client-1"
    assert "client_secret" not in stored
    assert stored["client_secret_hash"] != "secret-value"

    fake_redis.ttls[key] = 100
    assert store.get_client("client-1") == stored
    assert fake_redis.ttls[key] == CLIENT_TTL_SECONDS


def test_validate_client_credentials_uses_hash(
    store: OAuthStateStore,
) -> None:
    store.save_client(
        "client-1",
        {
            "client_id": "client-1",
            "client_secret": "secret-value",
            "token_endpoint_auth_method": "client_secret_post",
        },
    )

    assert store.validate_client_credentials("client-1", "secret-value") is True
    assert store.validate_client_credentials("client-1", "wrong-secret") is False


def test_validate_public_client_without_secret(
    store: OAuthStateStore,
) -> None:
    store.save_client(
        "client-1",
        {
            "client_id": "client-1",
            "token_endpoint_auth_method": "none",
        },
    )

    assert store.validate_client_credentials("client-1") is True


def test_list_clients_excludes_secret_hash(
    store: OAuthStateStore,
) -> None:
    store.save_client(
        "client-1",
        {
            "client_id": "client-1",
            "client_secret": "secret-value",
            "token_endpoint_auth_method": "client_secret_post",
            "client_name": "Test Client",
            "grant_types": ["authorization_code"],
            "registered_at": 123,
            "ip_address": "127.0.0.1",
        },
    )

    assert store.list_clients() == [
        {
            "client_id": "client-1",
            "client_name": "Test Client",
            "grant_types": ["authorization_code"],
            "registered_at": 123,
            "ip_address": "127.0.0.1",
        }
    ]


def test_consume_authcode_is_single_use(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_authcode("code-1", {"client_id": "client-1"})

    assert fake_redis.ttls["jarvis-auth-server-test:oauth:authcode:code-1"] == AUTH_CODE_TTL_SECONDS
    assert store.get_authcode("code-1") == {"client_id": "client-1"}
    assert store.consume_authcode("code-1") == {"client_id": "client-1"}
    assert store.consume_authcode("code-1") is None


def test_rotate_refresh_token_consumes_old_token_and_creates_new_token(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_refresh_token(
        "old-token",
        {
            "client_id": "client-1",
            "user_info": {"username": "user-1"},
            "scope": "servers-read",
            "expires_at": 100,
        },
    )

    assert store.get_refresh_token("old-token") == {
        "client_id": "client-1",
        "user_info": {"username": "user-1"},
        "scope": "servers-read",
        "expires_at": 100,
    }

    new_token_data = {
        "client_id": "client-1",
        "user_info": {"username": "user-1"},
        "scope": "servers-read",
        "expires_at": 200,
    }
    old_data = store.rotate_refresh_token("old-token", "new-token", new_data=new_token_data)

    assert old_data == {
        "client_id": "client-1",
        "user_info": {"username": "user-1"},
        "scope": "servers-read",
        "expires_at": 100,
    }
    assert "jarvis-auth-server-test:oauth:refresh:old-token" not in fake_redis.values

    new_key = "jarvis-auth-server-test:oauth:refresh:new-token"
    new_data = json.loads(fake_redis.values[new_key])
    assert new_data["client_id"] == "client-1"
    assert new_data["user_info"] == {"username": "user-1"}
    assert new_data["scope"] == "servers-read"
    assert new_data["expires_at"] == 200
    assert fake_redis.ttls[new_key] == REFRESH_TOKEN_TTL_SECONDS
    assert store.rotate_refresh_token("old-token", "another-token", new_data=new_token_data) is None


def test_rotate_refresh_token_preserves_json_array_shapes(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_refresh_token(
        "old-token",
        {
            "client_id": "client-1",
            "user_info": {"username": "user-1", "groups": []},
            "scope": "servers-read",
        },
    )

    store.rotate_refresh_token(
        "old-token",
        "new-token",
        new_data={"client_id": "client-1", "user_info": {"username": "user-1", "groups": []}, "scope": "servers-read"},
    )

    new_key = "jarvis-auth-server-test:oauth:refresh:new-token"
    new_data = json.loads(fake_redis.values[new_key])
    assert new_data["user_info"]["groups"] == []


def test_save_device_authorization_writes_device_and_user_code_in_transaction(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_device_authorization(
        device_code="device-1",
        user_code="USER-1234",
        data={"status": "pending"},
        ttl_seconds=600,
    )

    device_key = "jarvis-auth-server-test:oauth:device:device-1"
    user_code_key = "jarvis-auth-server-test:oauth:user_code:USER-1234"

    assert fake_redis.last_pipeline_transaction is True
    assert json.loads(fake_redis.values[device_key]) == {"status": "pending"}
    assert fake_redis.values[user_code_key] == "device-1"
    assert fake_redis.ttls[device_key] == 600
    assert fake_redis.ttls[user_code_key] == 600


def test_get_update_and_delete_device_state(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_device_code("device-1", {"status": "pending"}, 600)

    assert store.get_device_code("device-1") == {"status": "pending"}
    assert store.update_device_code("device-1", {"status": "approved"}) is True
    assert store.get_device_code("device-1") == {"status": "approved"}
    assert fake_redis.ttls["jarvis-auth-server-test:oauth:device:device-1"] == 600

    store.save_user_code("USER-1234", "device-1", 600)
    assert store.get_user_code("USER-1234") == "device-1"
    store.delete_user_code("USER-1234")
    assert store.get_user_code("USER-1234") is None


def test_consume_device_code_is_single_use(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_device_code("device-1", {"status": "approved"}, 600)

    assert store.consume_device_code("device-1") == {"status": "approved"}
    assert store.consume_device_code("device-1") is None
    assert store.get_device_code("device-1") is None


def test_atomically_consumes_approved_device_and_saves_refresh_token(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
) -> None:
    store.save_device_code(
        "device-1",
        {
            "status": "approved",
            "client_id": "client-1",
            "user_id": "user-1",
            "server_path": "github",
        },
        600,
    )

    consumed = store.consume_device_code_and_save_refresh_token(
        "device-1",
        "refresh-1",
        {"client_id": "client-1", "user_id": "user-1", "server_path": "github"},
        expected_client_id="client-1",
        expected_user_id="user-1",
        expected_server_path="github",
    )

    assert consumed is not None
    assert consumed["status"] == "approved"
    assert store.get_device_code("device-1") is None
    refresh_data = store.get_refresh_token("refresh-1")
    assert refresh_data is not None
    assert refresh_data["client_id"] == "client-1"
    assert "expires_at" in refresh_data
    assert fake_redis.ttls["jarvis-auth-server-test:oauth:refresh:refresh-1"] == REFRESH_TOKEN_TTL_SECONDS


def test_atomic_device_issue_preserves_code_when_binding_changed(store: OAuthStateStore) -> None:
    store.save_device_code(
        "device-1",
        {
            "status": "denied",
            "client_id": "client-1",
            "user_id": "user-1",
            "server_path": "github",
        },
        600,
    )

    consumed = store.consume_device_code_and_save_refresh_token(
        "device-1",
        "refresh-1",
        {"client_id": "client-1", "user_id": "user-1", "server_path": "github"},
        expected_client_id="client-1",
        expected_user_id="user-1",
        expected_server_path="github",
    )

    assert consumed is None
    assert store.get_device_code("device-1")["status"] == "denied"
    assert store.get_refresh_token("refresh-1") is None


@pytest.mark.parametrize("ttl_value", [-2, -1, 0])
def test_update_device_code_returns_false_when_ttl_is_not_positive(
    store: OAuthStateStore,
    fake_redis: _FakeRedis,
    ttl_value: int,
) -> None:
    key = "jarvis-auth-server-test:oauth:device:device-1"
    if ttl_value != -2:
        fake_redis.values[key] = json.dumps({"status": "pending"})
        fake_redis.ttls[key] = None if ttl_value == -1 else ttl_value

    assert store.update_device_code("device-1", {"status": "approved"}) is False


def test_oauth_state_store_keeps_all_state_under_one_prefix(fake_redis: _FakeRedis) -> None:
    store = OAuthStateStore(
        redis_client=fake_redis,
        key_prefix="jarvis-auth-server",
        client_secret_hash_key="hash-key",
    )

    store.save_client("client-1", {"client_id": "client-1", "token_endpoint_auth_method": "none"})
    store.save_refresh_token("rt-1", {"client_id": "client-1", "user_id": "u1"})
    store.save_authcode("code-1", {"client_id": "client-1"})

    assert "jarvis-auth-server:oauth:client:client-1" in fake_redis.values
    assert "jarvis-auth-server:oauth:refresh:rt-1" in fake_redis.values
    assert "jarvis-auth-server:oauth:authcode:code-1" in fake_redis.values


def test_oauth_client_store_is_explicit_client_facade(fake_redis: _FakeRedis) -> None:
    client_store = OAuthClientStore(
        redis_client=fake_redis,
        key_prefix="jarvis-auth-server",
        client_secret_hash_key="hash-key",
    )

    client_store.save_client("client-1", {"client_id": "client-1", "token_endpoint_auth_method": "none"})

    assert client_store.get_client("client-1") == {"client_id": "client-1", "token_endpoint_auth_method": "none"}
    assert "jarvis-auth-server:oauth:client:client-1" in fake_redis.values


def test_downstream_oauth_state_store_composes_client_and_refresh_stores(fake_redis: _FakeRedis) -> None:
    client_store = OAuthClientStore(
        redis_client=fake_redis,
        key_prefix="jarvis-auth-server",
        client_secret_hash_key="hash-key",
    )
    refresh_store = OAuthStateStore(
        redis_client=fake_redis,
        key_prefix="jarvis-registry",
        client_secret_hash_key="hash-key",
    )
    downstream_store = DownstreamOAuthStateStore(
        client_store=client_store,
        refresh_token_store=refresh_store,
        device_store=refresh_store,
    )

    client_store.save_client("client-1", {"client_id": "client-1", "token_endpoint_auth_method": "none"})
    downstream_store.save_refresh_token("rt-1", {"client_id": "client-1", "user_id": "u1"})
    downstream_store.save_device_authorization(
        "device-1",
        "ABCD-EFGH",
        {"client_id": "client-1", "status": "pending"},
        900,
    )

    assert downstream_store.get_client("client-1") is not None
    assert downstream_store.get_refresh_token("rt-1")["user_id"] == "u1"
    assert downstream_store.get_user_code("ABCD-EFGH") == "device-1"
    assert downstream_store.get_device_code("device-1")["status"] == "pending"
    assert "jarvis-auth-server:oauth:client:client-1" in fake_redis.values
    assert "jarvis-registry:oauth:refresh:rt-1" in fake_redis.values
    assert "jarvis-registry:oauth:device:device-1" in fake_redis.values
