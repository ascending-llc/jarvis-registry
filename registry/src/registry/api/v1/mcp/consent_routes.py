"""Consent APIs for MCP direct-connect authorization flows."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore
from registry_pkgs.core.oauth_state_store import DownstreamOAuthStoreProtocol

from ....auth.dependencies import CurrentUser
from ....deps import (
    get_consent_store,
    get_mcp_service,
    get_oauth_state_store,
    get_pending_consent_store,
    get_server_service,
)
from ....services.oauth.mcp_service import MCPService
from ....services.server_service import ServerServiceV1
from .oauth_router import _build_downstream_authorize_redirect

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
