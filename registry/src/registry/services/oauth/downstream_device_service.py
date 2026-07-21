"""Business logic for the per-server downstream Device Authorization Grant."""

import logging
import secrets
import time
from dataclasses import dataclass

from bson import ObjectId
from fastapi import status

from registry_pkgs.core.consent_store import PendingConsentStore
from registry_pkgs.core.downstream_oauth import (
    DEVICE_CODE_GRANT_TYPE,
    DeviceCodeResponse,
    generate_user_code,
    normalize_user_code,
)
from registry_pkgs.core.oauth_state_store import DownstreamOAuthStoreProtocol

from ...constants import DownstreamOAuthConstants
from ...core.config import settings
from ..server_service import ServerServiceV1
from .mcp_service import MCPService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceAuthorizationError(Exception):
    """OAuth-shaped validation failure returned by the public device endpoint."""

    error: str
    description: str
    status_code: int = status.HTTP_400_BAD_REQUEST


class DeviceCodeNotFoundError(ValueError):
    """Raised when a user code does not identify a pending authorization."""


async def create_device_authorization(
    *,
    user_id: str,
    server_path: str,
    client_id: str,
    scope: str | None,
    server_service: ServerServiceV1,
    store: DownstreamOAuthStoreProtocol,
    pending_store: PendingConsentStore,
) -> DeviceCodeResponse:
    """Validate a device request and persist its device-code and consent state."""
    if not ObjectId.is_valid(user_id):
        raise DeviceAuthorizationError("invalid_request", f"invalid user_id: {user_id}")

    registered_path = await server_service.extract_server_path(f"/{server_path}")
    server = await server_service.get_server_by_path(registered_path) if registered_path else None
    if not server:
        raise DeviceAuthorizationError(
            "invalid_request",
            f"server not found for path '{server_path}'",
            status.HTTP_404_NOT_FOUND,
        )

    client_metadata = store.get_client(client_id)
    if client_metadata is None:
        raise DeviceAuthorizationError("invalid_client", "unknown client_id")
    if DEVICE_CODE_GRANT_TYPE not in (client_metadata.get("grant_types") or []):
        raise DeviceAuthorizationError(
            "unauthorized_client",
            "client is not registered for the device_code grant type",
        )

    device_code = secrets.token_urlsafe(32)
    user_code = generate_user_code()
    nonce = secrets.token_urlsafe(32)
    current_time = int(time.time())
    expires_at = current_time + DownstreamOAuthConstants.DEVICE_CODE_TTL_SECONDS

    store.save_device_authorization(
        device_code=device_code,
        user_code=user_code,
        data={
            "user_id": user_id,
            "server_path": server_path,
            "client_id": client_id,
            "scope": scope or DownstreamOAuthConstants.PROXY_OPS_SCOPE,
            "status": "pending",
            "created_at": current_time,
            "expires_at": expires_at,
            "nonce": nonce,
        },
        # Redis TTL intentionally outlives expires_at by the grace period (see constants.py) — the
        # token endpoint's own expires_at check is what enforces the real 900s deadline.
        ttl_seconds=DownstreamOAuthConstants.DEVICE_CODE_TTL_SECONDS
        + DownstreamOAuthConstants.DEVICE_CODE_GRACE_PERIOD_SECONDS,
    )
    try:
        pending_store.save(
            nonce,
            {
                "flow_type": "device",
                "user_id": user_id,
                "client_id": client_id,
                "server_path": server_path,
                "device_code": device_code,
            },
            ttl_seconds=DownstreamOAuthConstants.DEVICE_CODE_TTL_SECONDS,
        )
    except Exception:
        try:
            store.consume_device_code(device_code)
        except Exception:
            logger.exception("Failed to clean up device code after pending-consent persistence failed")
        try:
            store.delete_user_code(user_code)
        except Exception:
            logger.exception("Failed to clean up user code after pending-consent persistence failed")
        raise

    verification_uri = f"{settings.registry_client_url.rstrip('/')}/device"
    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=f"{verification_uri}?user_code={user_code}",
        expires_in=DownstreamOAuthConstants.DEVICE_CODE_TTL_SECONDS,
        interval=DownstreamOAuthConstants.DEVICE_CODE_POLL_INTERVAL_SECONDS,
    )


def resolve_device_nonce(
    user_code: str,
    store: DownstreamOAuthStoreProtocol,
) -> str:
    """Resolve a human-entered code to the stable pending-consent nonce."""
    device_code = store.get_user_code(normalize_user_code(user_code))
    device_data = store.get_device_code(device_code) if device_code else None
    if device_data is None or device_data.get("status") != "pending":
        raise DeviceCodeNotFoundError

    nonce = device_data.get("nonce")
    if not isinstance(nonce, str):
        raise DeviceCodeNotFoundError
    return nonce


async def initiate_device_layer_a(
    *,
    user_id: str,
    server_path: str,
    device_code: str,
    mcp_service: MCPService,
    server_service: ServerServiceV1,
) -> str | None:
    """Start the real downstream-provider OAuth flow without a Layer B redirect context."""
    registered_path = await server_service.extract_server_path(f"/{server_path}")
    server = await server_service.get_server_by_path(registered_path) if registered_path else None
    if not server:
        return None

    _, auth_url, error = await mcp_service.oauth_service.initiate_oauth_flow(
        user_id=user_id,
        server=server,
        device_code=device_code,
    )
    return None if error else auth_url


def mark_device_denied(
    device_code: str,
    store: DownstreamOAuthStoreProtocol,
) -> bool:
    """Make a consent denial immediately visible to the polling client."""
    device_data = store.get_device_code(device_code)
    if device_data is None:
        return False
    return store.update_device_code(device_code, {**device_data, "status": "denied"})


def mark_device_failed(
    device_code: str,
    store: DownstreamOAuthStoreProtocol,
) -> bool:
    """Move an authorization to a terminal error state when Layer A cannot start."""
    device_data = store.get_device_code(device_code)
    if device_data is None:
        return False
    return store.update_device_code(device_code, {**device_data, "status": "failed"})


def mark_device_approved(
    device_code: str,
    user_id: str,
    store: DownstreamOAuthStoreProtocol,
) -> bool:
    """Approve a device code only after the real downstream OAuth flow completes."""
    device_data = store.get_device_code(device_code)
    if device_data is None:
        return False
    return store.update_device_code(
        device_code,
        {**device_data, "status": "approved", "user_id": user_id},
    )
