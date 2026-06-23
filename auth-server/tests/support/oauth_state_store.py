"""In-memory OAuth state store used by auth-server route tests."""

import time
from typing import Any

from auth_server.services.oauth_state_store import REFRESH_TOKEN_TTL_SECONDS, OAuthStateStoreProtocol


class InMemoryOAuthStateStore(OAuthStateStoreProtocol):
    def __init__(self) -> None:
        self.registered_clients: dict[str, dict[str, Any]] = {}
        self.authorization_codes_storage: dict[str, dict[str, Any]] = {}
        self.refresh_tokens_storage: dict[str, dict[str, Any]] = {}
        self.device_codes_storage: dict[str, dict[str, Any]] = {}
        self.user_codes_storage: dict[str, str] = {}

    def clear(self) -> None:
        self.registered_clients.clear()
        self.authorization_codes_storage.clear()
        self.refresh_tokens_storage.clear()
        self.device_codes_storage.clear()
        self.user_codes_storage.clear()

    def save_client(self, client_id: str, metadata: dict[str, Any]) -> None:
        self.registered_clients[client_id] = dict(metadata)

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        return self.registered_clients.get(client_id)

    def validate_client_credentials(
        self,
        client_id: str,
        client_secret: str | None = None,
    ) -> bool:
        client_metadata = self.get_client(client_id)
        if client_metadata is None:
            return False
        if client_metadata.get("token_endpoint_auth_method") == "client_secret_post":
            return client_metadata.get("client_secret") == client_secret
        return True

    def list_clients(self) -> list[dict[str, Any]]:
        return [
            {
                "client_id": client_id,
                "client_name": metadata.get("client_name"),
                "grant_types": metadata.get("grant_types"),
                "registered_at": metadata.get("registered_at"),
                "ip_address": metadata.get("ip_address"),
            }
            for client_id, metadata in self.registered_clients.items()
        ]

    def save_authcode(self, code: str, data: dict[str, Any]) -> None:
        self.authorization_codes_storage[code] = dict(data)

    def get_authcode(self, code: str) -> dict[str, Any] | None:
        return self.authorization_codes_storage.get(code)

    def consume_authcode(self, code: str) -> dict[str, Any] | None:
        return self.authorization_codes_storage.pop(code, None)

    def save_refresh_token(self, token: str, data: dict[str, Any]) -> None:
        stored_data = dict(data)
        stored_data.setdefault("expires_at", int(time.time()) + REFRESH_TOKEN_TTL_SECONDS)
        self.refresh_tokens_storage[token] = stored_data

    def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        token_data = self.refresh_tokens_storage.get(token)
        if token_data is None:
            return None
        expires_at = token_data.get("expires_at")
        if isinstance(expires_at, int) and int(time.time()) > expires_at:
            self.refresh_tokens_storage.pop(token, None)
            return None
        return token_data

    def rotate_refresh_token(
        self,
        old_token: str,
        new_token: str,
    ) -> dict[str, Any] | None:
        old_data = self.refresh_tokens_storage.pop(old_token, None)
        if old_data is None:
            return None
        self.refresh_tokens_storage[new_token] = dict(old_data)
        return old_data

    def save_device_authorization(
        self,
        device_code: str,
        user_code: str,
        data: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        self.save_device_code(device_code, data, ttl_seconds)
        self.save_user_code(user_code, device_code, ttl_seconds)

    def save_device_code(
        self,
        device_code: str,
        data: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        self.device_codes_storage[device_code] = dict(data)

    def get_device_code(self, device_code: str) -> dict[str, Any] | None:
        return self.device_codes_storage.get(device_code)

    def update_device_code(
        self,
        device_code: str,
        data: dict[str, Any],
    ) -> bool:
        if device_code not in self.device_codes_storage:
            return False
        self.device_codes_storage[device_code] = dict(data)
        return True

    def save_user_code(
        self,
        user_code: str,
        device_code: str,
        ttl_seconds: int,
    ) -> None:
        self.user_codes_storage[user_code] = device_code

    def get_user_code(self, user_code: str) -> str | None:
        return self.user_codes_storage.get(user_code)

    def delete_user_code(self, user_code: str) -> None:
        self.user_codes_storage.pop(user_code, None)


test_oauth_state_store = InMemoryOAuthStateStore()
registered_clients = test_oauth_state_store.registered_clients
authorization_codes_storage = test_oauth_state_store.authorization_codes_storage
refresh_tokens_storage = test_oauth_state_store.refresh_tokens_storage
device_codes_storage = test_oauth_state_store.device_codes_storage
user_codes_storage = test_oauth_state_store.user_codes_storage
