"""Unit tests for downstream Device Authorization Grant service helpers."""

from unittest.mock import AsyncMock, Mock

import pytest

from registry.services.oauth.downstream_device_service import (
    DeviceCodeNotFoundError,
    create_device_authorization,
    mark_device_approved,
    mark_device_denied,
    mark_device_failed,
    resolve_device_nonce,
)


def _stateful_store(initial_state: dict | None = None) -> Mock:
    store = Mock()
    state = dict(initial_state) if initial_state is not None else None

    def get_device_code(device_code: str) -> dict | None:
        return dict(state) if state is not None else None

    def update_device_code(device_code: str, data: dict) -> bool:
        nonlocal state
        state = dict(data)
        return True

    store.get_user_code.return_value = "device-1"
    store.get_device_code.side_effect = get_device_code
    store.update_device_code.side_effect = update_device_code
    store.current_state = lambda: state
    return store


def test_resolve_device_nonce_normalizes_code_and_returns_stable_nonce() -> None:
    store = _stateful_store({"status": "pending", "nonce": "nonce-1"})

    nonce = resolve_device_nonce(" abcd efgh ", store)

    assert nonce == "nonce-1"
    store.get_user_code.assert_called_once_with("ABCD-EFGH")


@pytest.mark.parametrize("state", [None, {"status": "approved", "nonce": "nonce-1"}, {"status": "pending"}])
def test_resolve_device_nonce_rejects_invalid_state(state: dict | None) -> None:
    store = _stateful_store(state)

    with pytest.raises(DeviceCodeNotFoundError):
        resolve_device_nonce("ABCD-EFGH", store)


def test_device_status_helpers_preserve_binding_fields() -> None:
    store = _stateful_store(
        {
            "status": "pending",
            "user_id": "requested-user",
            "client_id": "client-1",
            "server_path": "github",
        }
    )

    assert mark_device_denied("device-1", store) is True
    assert store.current_state()["status"] == "denied"

    assert mark_device_failed("device-1", store) is True
    assert store.current_state()["status"] == "failed"

    assert mark_device_approved("device-1", "verified-user", store) is True
    assert store.current_state() == {
        "status": "approved",
        "user_id": "verified-user",
        "client_id": "client-1",
        "server_path": "github",
    }


@pytest.mark.asyncio
async def test_create_device_authorization_cleans_up_when_pending_consent_save_fails() -> None:
    server_service = Mock()
    server_service.extract_server_path = AsyncMock(return_value="/github")
    server_service.get_server_by_path = AsyncMock(return_value=Mock())
    store = Mock()
    store.get_client.return_value = {
        "grant_types": ["urn:ietf:params:oauth:grant-type:device_code"],
    }
    pending_store = Mock()
    pending_store.save.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await create_device_authorization(
            user_id="507f1f77bcf86cd799439011",
            server_path="github",
            client_id="client-1",
            scope=None,
            server_service=server_service,
            store=store,
            pending_store=pending_store,
        )

    device_code = store.save_device_authorization.call_args.kwargs["device_code"]
    user_code = store.save_device_authorization.call_args.kwargs["user_code"]
    store.consume_device_code.assert_called_once_with(device_code)
    store.delete_user_code.assert_called_once_with(user_code)
