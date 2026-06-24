"""Redis-backed OAuth state storage for auth-server."""

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Protocol

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

CLIENT_TTL_SECONDS = 30 * 24 * 3600
AUTH_CODE_TTL_SECONDS = 600
REFRESH_TOKEN_TTL_SECONDS = 14 * 24 * 3600

_CONSUME_AUTHCODE_SCRIPT = """
local val = redis.call('GET', KEYS[1])
if not val then return nil end
redis.call('DEL', KEYS[1])
return val
"""

_ROTATE_REFRESH_TOKEN_SCRIPT = """
local old_key = KEYS[1]
local new_key = KEYS[2]
local ttl = tonumber(ARGV[1])
local new_val = ARGV[2]

local val = redis.call('GET', old_key)
if not val then return nil end

redis.call('DEL', old_key)
redis.call('SET', new_key, new_val, 'EX', ttl)
return val
"""


class OAuthStateStoreProtocol(Protocol):
    def save_client(self, client_id: str, metadata: dict[str, Any]) -> None: ...

    def get_client(self, client_id: str) -> dict[str, Any] | None: ...

    def validate_client_credentials(
        self,
        client_id: str,
        client_secret: str | None = None,
    ) -> bool: ...

    def list_clients(self) -> list[dict[str, Any]]: ...

    def save_authcode(self, code: str, data: dict[str, Any]) -> None: ...

    def get_authcode(self, code: str) -> dict[str, Any] | None: ...

    def consume_authcode(self, code: str) -> dict[str, Any] | None: ...

    def save_refresh_token(self, token: str, data: dict[str, Any]) -> None: ...

    def get_refresh_token(self, token: str) -> dict[str, Any] | None: ...

    def rotate_refresh_token(
        self,
        old_token: str,
        new_token: str,
        new_data: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    def save_device_authorization(
        self,
        device_code: str,
        user_code: str,
        data: dict[str, Any],
        ttl_seconds: int,
    ) -> None: ...

    def save_device_code(
        self,
        device_code: str,
        data: dict[str, Any],
        ttl_seconds: int,
    ) -> None: ...

    def get_device_code(self, device_code: str) -> dict[str, Any] | None: ...

    def update_device_code(
        self,
        device_code: str,
        data: dict[str, Any],
    ) -> bool: ...

    def save_user_code(
        self,
        user_code: str,
        device_code: str,
        ttl_seconds: int,
    ) -> None: ...

    def get_user_code(self, user_code: str) -> str | None: ...

    def delete_user_code(self, user_code: str) -> None: ...


def _decode_redis_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


class OAuthStateStore:
    """Persist OAuth flow state in Redis.

    Client listing scans Redis keys and is intended for admin/debug use only.
    Do not call it from request hot paths.
    """

    def __init__(
        self,
        redis_client: Redis,
        key_prefix: str,
        client_secret_hash_key: str,
        client_key_prefix: str | None = None,
    ) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._client_secret_hash_key = client_secret_hash_key
        self._client_key_prefix = client_key_prefix or key_prefix

    def save_client(self, client_id: str, metadata: dict[str, Any]) -> None:
        stored_metadata = self._prepare_client_metadata(metadata)
        self._set_json(self._client_key(client_id), stored_metadata, CLIENT_TTL_SECONDS)

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        key = self._client_key(client_id)
        client_metadata = self._get_json(key)
        if client_metadata is None:
            return None

        try:
            self._redis.expire(key, CLIENT_TTL_SECONDS)
        except RedisError:
            logger.exception("Failed to extend OAuth client TTL for client_id=%s", client_id)
            raise

        return client_metadata

    def validate_client_credentials(
        self,
        client_id: str,
        client_secret: str | None = None,
    ) -> bool:
        client_metadata = self.get_client(client_id)
        if client_metadata is None:
            return False

        if client_metadata.get("token_endpoint_auth_method") != "client_secret_post":
            return True

        expected_hash = client_metadata.get("client_secret_hash")
        if not isinstance(expected_hash, str) or client_secret is None:
            return False

        provided_hash = self._hash_client_secret(client_secret)
        return hmac.compare_digest(expected_hash, provided_hash)

    def list_clients(self) -> list[dict[str, Any]]:
        try:
            clients = []
            for key in self._redis.scan_iter(match=self._client_key("*"), count=100):
                client_metadata = self._get_json_without_ttl_slide(key)
                if client_metadata is None:
                    continue
                clients.append(
                    {
                        "client_id": client_metadata.get("client_id"),
                        "client_name": client_metadata.get("client_name"),
                        "grant_types": client_metadata.get("grant_types"),
                        "registered_at": client_metadata.get("registered_at"),
                        "ip_address": client_metadata.get("ip_address"),
                    }
                )
            return clients
        except RedisError:
            logger.exception("Failed to list OAuth clients from Redis")
            return []

    def save_authcode(self, code: str, data: dict[str, Any]) -> None:
        self._set_json(self._authcode_key(code), data, AUTH_CODE_TTL_SECONDS)

    def get_authcode(self, code: str) -> dict[str, Any] | None:
        return self._get_json(self._authcode_key(code))

    def consume_authcode(self, code: str) -> dict[str, Any] | None:
        try:
            raw_data = self._redis.eval(_CONSUME_AUTHCODE_SCRIPT, 1, self._authcode_key(code))
        except RedisError:
            logger.exception("Failed to consume OAuth authorization code")
            raise

        return self._loads_json(raw_data)

    def save_refresh_token(self, token: str, data: dict[str, Any]) -> None:
        stored_data = dict(data)
        stored_data.setdefault("expires_at", int(time.time()) + REFRESH_TOKEN_TTL_SECONDS)
        self._set_json(self._refresh_key(token), stored_data, REFRESH_TOKEN_TTL_SECONDS)

    def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        return self._get_json(self._refresh_key(token))

    def rotate_refresh_token(
        self,
        old_token: str,
        new_token: str,
        new_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            raw_old_data = self._redis.eval(
                _ROTATE_REFRESH_TOKEN_SCRIPT,
                2,
                self._refresh_key(old_token),
                self._refresh_key(new_token),
                REFRESH_TOKEN_TTL_SECONDS,
                self._dumps_json(new_data),
            )
        except RedisError:
            logger.exception("Failed to rotate OAuth refresh token")
            raise

        return self._loads_json(raw_old_data)

    def save_device_authorization(
        self,
        device_code: str,
        user_code: str,
        data: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.set(self._device_key(device_code), self._dumps_json(data), ex=ttl_seconds)
            pipe.set(self._user_code_key(user_code), device_code, ex=ttl_seconds)
            pipe.execute()
        except RedisError:
            logger.exception("Failed to save OAuth device authorization state")
            raise

    def save_device_code(
        self,
        device_code: str,
        data: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        self._set_json(self._device_key(device_code), data, ttl_seconds)

    def get_device_code(self, device_code: str) -> dict[str, Any] | None:
        return self._get_json(self._device_key(device_code))

    def update_device_code(
        self,
        device_code: str,
        data: dict[str, Any],
    ) -> bool:
        key = self._device_key(device_code)

        try:
            remaining_ttl = self._redis.ttl(key)
            if remaining_ttl <= 0:
                if remaining_ttl == -1:
                    logger.warning("OAuth device code key exists without TTL: %s", key)
                return False

            self._redis.set(key, self._dumps_json(data), ex=remaining_ttl)
            return True
        except RedisError:
            logger.exception("Failed to update OAuth device code")
            raise

    def save_user_code(
        self,
        user_code: str,
        device_code: str,
        ttl_seconds: int,
    ) -> None:
        try:
            self._redis.set(self._user_code_key(user_code), device_code, ex=ttl_seconds)
        except RedisError:
            logger.exception("Failed to save OAuth user code")
            raise

    def get_user_code(self, user_code: str) -> str | None:
        try:
            return _decode_redis_value(self._redis.get(self._user_code_key(user_code)))
        except RedisError:
            logger.exception("Failed to get OAuth user code")
            raise

    def delete_user_code(self, user_code: str) -> None:
        try:
            self._redis.delete(self._user_code_key(user_code))
        except RedisError:
            logger.exception("Failed to delete OAuth user code")
            raise

    def _client_key(self, client_id: str) -> str:
        return f"{self._client_key_prefix}:oauth:client:{client_id}"

    def _authcode_key(self, code: str) -> str:
        return self._key("authcode", code)

    def _refresh_key(self, token: str) -> str:
        return self._key("refresh", token)

    def _device_key(self, device_code: str) -> str:
        return self._key("device", device_code)

    def _user_code_key(self, user_code: str) -> str:
        return self._key("user_code", user_code)

    def _key(self, state_type: str, state_id: str) -> str:
        return f"{self._key_prefix}:oauth:{state_type}:{state_id}"

    def _prepare_client_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        stored_metadata = dict(metadata)
        client_secret = stored_metadata.pop("client_secret", None)

        if isinstance(client_secret, str):
            stored_metadata["client_secret_hash"] = self._hash_client_secret(client_secret)

        return stored_metadata

    def _hash_client_secret(self, client_secret: str) -> str:
        return hmac.new(
            self._client_secret_hash_key.encode("utf-8"),
            client_secret.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _set_json(
        self,
        key: str,
        data: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        try:
            self._redis.set(key, self._dumps_json(data), ex=ttl_seconds)
        except RedisError:
            logger.exception("Failed to save OAuth state to Redis key=%s", key)
            raise

    def _get_json(self, key: str) -> dict[str, Any] | None:
        try:
            return self._loads_json(self._redis.get(key))
        except RedisError:
            logger.exception("Failed to get OAuth state from Redis key=%s", key)
            raise

    def _get_json_without_ttl_slide(self, key: str | bytes) -> dict[str, Any] | None:
        redis_key = _decode_redis_value(key)
        if redis_key is None:
            return None
        return self._get_json(redis_key)

    def _dumps_json(self, data: dict[str, Any]) -> str:
        return json.dumps(data, separators=(",", ":"))

    def _loads_json(self, raw_data: Any) -> dict[str, Any] | None:
        decoded = _decode_redis_value(raw_data)
        if decoded is None:
            return None
        loaded = json.loads(decoded)
        if not isinstance(loaded, dict):
            raise TypeError("OAuth Redis state payload must be a JSON object")
        return loaded
