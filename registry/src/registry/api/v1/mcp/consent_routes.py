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
from ....services.oauth.mcp_service import MCPService
from ....services.server_service import ServerServiceV1
from .oauth_router import _build_downstream_authorize_redirect, _notify_elicitation_complete

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-consent"])


class ApproveConsentRequest(BaseModel):
    nonce: str


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
        pending = pending_store.consume(body.nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

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
        pending = pending_store.consume(body.nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

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
) -> dict[str, str | None]:
    """Shared deny logic for both consent flows.

    Denying records nothing new: the pending record is proactively removed (rather than left to
    expire on its own TTL) and no consent is granted, so the next call from this
    ``(user_id, client_id, server_path)`` is gated exactly as if the user had never clicked
    anything. The MCP client is still notified (when a live session exists) so a blocked tool
    call can retry immediately instead of waiting out its own timeout — the notification only
    means "the out-of-band step concluded," not "access was granted"; the retry itself hits the
    gate again and gets a fresh elicitation since consent was never recorded.
    """
    try:
        # Peek (non-destructive) before consuming: an ownership mismatch must not delete a nonce
        # that still legitimately belongs to its rightful owner.
        pending = pending_store.peek(nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        pending_store.consume(nonce)
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
) -> dict[str, str | None]:
    return await _deny_consent(body.nonce, user_context, pending_store, session_store, log_tag="Downstream Consent")


@router.post("/consent/server/deny")
async def deny_server_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    session_store: SessionStore = Depends(get_session_store),
) -> dict[str, str | None]:
    return await _deny_consent(body.nonce, user_context, pending_store, session_store, log_tag="Server Consent")
