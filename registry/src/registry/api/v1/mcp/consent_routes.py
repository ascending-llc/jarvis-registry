"""Consent APIs for MCP direct-connect authorization flows."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore
from registry_pkgs.core.oauth_state_store import DownstreamOAuthStoreProtocol

from ....auth.dependencies import CurrentUser
from ....core.session_store import SessionStore
from ....deps import (
    get_consent_store,
    get_mcp_service,
    get_oauth_state_store,
    get_pending_consent_store,
    get_server_service,
    get_session_store,
)
from ....services.oauth.downstream_device_service import (
    DeviceCodeNotFoundError,
    initiate_device_layer_a,
    mark_device_denied,
    mark_device_failed,
    resolve_device_nonce,
)
from ....services.oauth.mcp_service import MCPService
from ....services.server_service import ServerServiceV1
from .oauth_router import _build_downstream_authorize_redirect, _notify_elicitation_complete

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-consent"])


class ApproveConsentRequest(BaseModel):
    nonce: str


@router.get("/consent/device/resolve")
async def resolve_device_code(
    user_code: str,
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
) -> dict[str, str]:
    """Resolve a human-entered code to the stable nonce consumed by the existing consent UI."""
    try:
        return {"nonce": resolve_device_nonce(user_code, store)}
    except DeviceCodeNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This code is invalid or has expired.",
        ) from e
    except Exception as e:
        logger.exception("[Downstream Consent] failed to resolve device code")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.get("/consent/downstream")
async def get_downstream_consent_context(
    nonce: str,
    user_context: CurrentUser,
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str | int | None]:
    try:
        pending = pending_store.peek(nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        client_metadata = store.get_client(pending["client_id"]) or {}
        return {
            "client_name": client_metadata.get("client_name", "Unknown application"),
            "client_uri": client_metadata.get("client_uri"),
            "ip_address": client_metadata.get("ip_address"),
            "registered_at": client_metadata.get("registered_at"),
            "server_path": pending["server_path"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Downstream Consent] failed to fetch consent context: user={user_context['user_id']}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/downstream")
async def approve_downstream_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
    server_service: ServerServiceV1 = Depends(get_server_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    consent_store: ConsentStore = Depends(get_consent_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str]:
    try:
        # Peek (non-destructive) before consuming: an ownership mismatch must not delete a nonce
        # that still legitimately belongs to its rightful owner.
        pending = pending_store.peek(body.nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        consumed = pending_store.consume(body.nonce)
        if consumed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        pending = consumed

        if pending.get("flow_type") == "device":
            try:
                auth_url = await initiate_device_layer_a(
                    user_id=pending["user_id"],
                    server_path=pending["server_path"],
                    device_code=pending["device_code"],
                    mcp_service=mcp_service,
                    server_service=server_service,
                )
            except Exception:
                if not mark_device_failed(pending["device_code"], store):
                    logger.warning("Device authorization expired while recording a Layer A startup failure")
                raise
            if auth_url is None:
                if not mark_device_failed(pending["device_code"], store):
                    logger.warning("Device authorization expired while recording a Layer A startup failure")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to initiate downstream OAuth flow",
                )
            try:
                consent_store.grant_client_consent(pending["user_id"], pending["client_id"])
            except Exception:
                if not mark_device_failed(pending["device_code"], store):
                    logger.warning("Device authorization expired while recording a consent persistence failure")
                raise
            return {"redirect_url": auth_url}

        consent_store.grant_client_consent(pending["user_id"], pending["client_id"])
        redirect = await _build_downstream_authorize_redirect(
            user_id=pending["user_id"],
            server_path=pending["server_path"],
            user_context=user_context,
            response_type=pending["response_type"],
            client_id=pending["client_id"],
            redirect_uri=pending["redirect_uri"],
            code_challenge=pending["code_challenge"],
            code_challenge_method=pending["code_challenge_method"],
            state=pending["state"],
            mcp_service=mcp_service,
            server_service=server_service,
            store=store,
            consent_store=consent_store,
            pending_store=pending_store,
        )
        redirect_url = redirect.headers.get("location")
        if not redirect_url:
            logger.error("Downstream consent approval did not produce a redirect URL")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

        return {"redirect_url": redirect_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Downstream Consent] failed to approve consent: user={user_context['user_id']}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.get("/consent/server")
async def get_server_consent_context(
    nonce: str,
    user_context: CurrentUser,
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    server_service: ServerServiceV1 = Depends(get_server_service),
) -> dict[str, str | int | None]:
    try:
        pending = pending_store.peek(nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        client_metadata = store.get_client(pending["client_id"]) or {}
        server = await server_service.get_server_by_path(pending["server_path"])
        return {
            "client_name": client_metadata.get("client_name", "Unknown application"),
            "client_uri": client_metadata.get("client_uri"),
            "ip_address": client_metadata.get("ip_address"),
            "registered_at": client_metadata.get("registered_at"),
            "server_path": pending["server_path"],
            "server_name": server.serverName if server else pending["server_path"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Server Consent] failed to fetch consent context: user={user_context['user_id']}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/server")
async def approve_server_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    consent_store: ConsentStore = Depends(get_consent_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    session_store: SessionStore = Depends(get_session_store),
) -> dict[str, str | None]:
    try:
        # Peek (non-destructive) before consuming: an ownership mismatch must not delete a nonce
        # that still legitimately belongs to its rightful owner.
        pending = pending_store.peek(body.nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        consumed = pending_store.consume(body.nonce)
        if consumed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        pending = consumed
        consent_store.grant_server_consent(pending["user_id"], pending["client_id"], pending["server_path"])

        # Best-effort: Mode 1 (mcpgw) pending records carry elicitation_id/client_branding so the
        # paused MCP session can be notified and the frontend can deep-link back. Mode 2
        # (direct-connect) records have no such session to notify, so this is a no-op for them.
        client_branding = await _notify_elicitation_complete(pending, session_store)
        return {"status": "ok", "client_branding": client_branding}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Server Consent] failed to approve consent: user={user_context['user_id']}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


async def _deny_consent(
    nonce: str,
    user_context: CurrentUser,
    pending_store: PendingConsentStore,
    session_store: SessionStore,
    *,
    log_tag: str,
    store: DownstreamOAuthStoreProtocol | None = None,
) -> dict[str, str | None]:
    """Shared deny logic for both consent flows.

    The pending record is proactively removed and no consent is granted. Device-flow denials are
    additionally persisted on the device code so the polling client immediately receives
    ``access_denied``; other consent flows record nothing new and are gated again on retry. A live
    MCP session is still notified that the out-of-band step concluded.
    """
    try:
        # Peek (non-destructive) before consuming: an ownership mismatch must not delete a nonce
        # that still legitimately belongs to its rightful owner.
        pending = pending_store.peek(nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        consumed = pending_store.consume(nonce)
        if consumed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        pending = consumed
        if pending.get("flow_type") == "device" and store is not None:
            if not mark_device_denied(pending["device_code"], store):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        client_branding = await _notify_elicitation_complete(pending, session_store)
        return {"status": "denied", "client_branding": client_branding}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[{log_tag}] failed to deny consent: user={user_context['user_id']}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/downstream/deny")
async def deny_downstream_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    session_store: SessionStore = Depends(get_session_store),
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
) -> dict[str, str | None]:
    return await _deny_consent(
        body.nonce,
        user_context,
        pending_store,
        session_store,
        log_tag="Downstream Consent",
        store=store,
    )


@router.post("/consent/server/deny")
async def deny_server_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    session_store: SessionStore = Depends(get_session_store),
) -> dict[str, str | None]:
    return await _deny_consent(body.nonce, user_context, pending_store, session_store, log_tag="Server Consent")
