"""Consent APIs for MCP direct-connect authorization flows."""

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from registry_pkgs.core.client_categories import requires_app_consent, resolve_client_category
from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore
from registry_pkgs.core.oauth_state_store import DownstreamOAuthStoreProtocol
from registry_pkgs.core.redirect_uri import build_oauth_error_redirect_url
from registry_pkgs.core.scopes import get_scope_description
from registry_pkgs.oauth.schemas import ConsentScopeDisplay, DownstreamConsentContext

from ....auth.dependencies import CurrentUser
from ....constants import PENDING_CONSENT_FLOW_FIELD, DownstreamOAuthConstants, PendingConsentFlow
from ....core.config import settings
from ....deps import (
    get_a2a_agent_service,
    get_consent_store,
    get_mcp_service,
    get_oauth_state_store,
    get_pending_consent_store,
    get_server_service,
)
from ....schemas.consent_api_schemas import ConsentErrorResponse
from ....services.a2a_agent_service import A2AAgentService
from ....services.oauth.downstream_device_service import (
    DeviceCodeNotFoundError,
    initiate_device_layer_a,
    mark_device_denied,
    mark_device_failed,
    persist_device_consent,
    resolve_device_nonce,
)
from ....services.oauth.mcp_service import MCPService
from ....services.server_service import ServerServiceV1
from ..consent_openapi import CONSENT_WRITE_ERRORS, CONSENT_WRITE_OPENAPI
from .oauth_router import (
    _DOWNSTREAM_ERROR_FLOW_TYPE,
    _build_downstream_authorize_redirect,
    _client_branding_from_meta,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-consent"])

_APP_REVOKED_DETAIL = "This app's access was revoked. Reconnect it from your AI app, then try again."
_CONSENT_NOT_FOUND = "This consent link has expired."
_CODE_FLOW_FIELDS = frozenset({"response_type", "redirect_uri", "code_challenge", "code_challenge_method", "state"})

_APPROVAL_RESPONSES = {
    **CONSENT_WRITE_ERRORS,
    404: {"model": ConsentErrorResponse, "description": "The consent link is unavailable for this approval."},
    409: {"model": ConsentErrorResponse, "description": _APP_REVOKED_DETAIL},
    500: {"model": ConsentErrorResponse, "description": "Approval could not be completed."},
}


def _pending_consent_flow(pending: dict[str, Any]) -> PendingConsentFlow | None:
    """Return the Registry consent page that owns a record from the shared pending-consent store."""
    if not pending.get("client_id"):
        return None
    if PENDING_CONSENT_FLOW_FIELD not in pending:
        return _untagged_pending_consent_flow(pending)
    try:
        return PendingConsentFlow(pending[PENDING_CONSENT_FLOW_FIELD])
    except ValueError:
        return None


def _untagged_pending_consent_flow(pending: dict[str, Any]) -> PendingConsentFlow | None:
    """Infer the page for a record saved before Registry tagged its records.

    Remove once every untagged record has expired: one device-code TTL after the rollout that added the tag.
    """
    if "flow_type" in pending or "device_code" in pending:
        # auth-server also writes flow_type "device" records, but never with a server_path.
        if pending.get("flow_type") == "device" and "server_path" in pending and "device_code" in pending:
            return PendingConsentFlow.DOWNSTREAM
        return None
    if not _CODE_FLOW_FIELDS.isdisjoint(pending):
        if _CODE_FLOW_FIELDS.issubset(pending) and "server_path" in pending and "agent_path" not in pending:
            return PendingConsentFlow.DOWNSTREAM
        return None
    if "server_path" in pending and "agent_path" not in pending:
        return PendingConsentFlow.SERVER
    if "agent_path" in pending and "server_path" not in pending:
        return PendingConsentFlow.AGENT
    return None


def _load_pending_consent(
    pending_store: PendingConsentStore,
    nonce: str,
    user_id: str | None,
    flow: PendingConsentFlow,
    *,
    consume: bool = False,
) -> dict[str, Any]:
    pending = pending_store.peek(nonce)
    if not user_id or pending is None or pending.get("user_id") != user_id or _pending_consent_flow(pending) != flow:
        raise HTTPException(status_code=404, detail=_CONSENT_NOT_FOUND)
    # GETDEL chooses a single winner if two decisions race or the link expires.
    if consume and pending_store.consume(nonce) != pending:
        raise HTTPException(status_code=404, detail=_CONSENT_NOT_FOUND)
    return pending


def _peek_downstream_error_consent(
    pending_store: PendingConsentStore,
    nonce: str,
) -> dict[str, Any] | None:
    pending = pending_store.peek(nonce)
    if pending is None or pending.get("flow_type") != _DOWNSTREAM_ERROR_FLOW_TYPE:
        return None
    return pending


def _consume_downstream_error_consent(
    pending_store: PendingConsentStore,
    nonce: str,
) -> dict[str, Any] | None:
    pending = pending_store.consume(nonce)
    if pending is None or pending.get("flow_type") != _DOWNSTREAM_ERROR_FLOW_TYPE:
        return None
    return pending


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


@router.get("/consent/downstream", response_model=DownstreamConsentContext)
async def get_downstream_consent_context(
    nonce: str,
    user_context: CurrentUser,
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> DownstreamConsentContext:
    try:
        pending = await asyncio.to_thread(
            _load_pending_consent, pending_store, nonce, user_context["user_id"], PendingConsentFlow.DOWNSTREAM
        )

        client_metadata = store.get_client(pending["client_id"]) or {}
        return DownstreamConsentContext(
            client_name=client_metadata.get("client_name", "Unknown application"),
            client_uri=client_metadata.get("client_uri"),
            redirect_uri=pending.get("redirect_uri"),
            ip_address=client_metadata.get("ip_address"),
            registered_at=client_metadata.get("registered_at"),
            server_path=pending["server_path"],
            scopes=[
                ConsentScopeDisplay(
                    name=DownstreamOAuthConstants.PROXY_OPS_SCOPE,
                    description=get_scope_description(DownstreamOAuthConstants.PROXY_OPS_SCOPE),
                )
            ],
        )
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
        pending = await asyncio.to_thread(
            _load_pending_consent,
            pending_store,
            body.nonce,
            user_context["user_id"],
            PendingConsentFlow.DOWNSTREAM,
            consume=True,
        )

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
                if not await asyncio.to_thread(mark_device_failed, pending["device_code"], store):
                    logger.warning("Device authorization expired while recording a Layer A startup failure")
                raise
            if auth_url is None:
                if not await asyncio.to_thread(mark_device_failed, pending["device_code"], store):
                    logger.warning("Device authorization expired while recording a Layer A startup failure")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to initiate downstream OAuth flow",
                )
            try:
                await asyncio.to_thread(
                    persist_device_consent,
                    device_code=pending["device_code"],
                    user_id=pending["user_id"],
                    client_id=pending["client_id"],
                    store=store,
                    consent_store=consent_store,
                )
            except Exception:
                if not await asyncio.to_thread(mark_device_failed, pending["device_code"], store):
                    logger.warning("Device authorization expired while recording a consent persistence failure")
                raise
            return {"redirect_url": auth_url}

        if pending["client_id"] != settings.jwt_token_config.registry_client_id:
            await asyncio.to_thread(consent_store.grant_client_consent, pending["user_id"], pending["client_id"])
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
        pending = await asyncio.to_thread(
            _load_pending_consent, pending_store, nonce, user_context["user_id"], PendingConsentFlow.SERVER
        )

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


def _approve_resource_consent(
    resource: Literal[PendingConsentFlow.SERVER, PendingConsentFlow.AGENT],
    nonce: str,
    user_id: str | None,
    pending_store: PendingConsentStore,
    consent_store: ConsentStore,
) -> dict[str, str | None]:
    try:
        path_field = f"{resource}_path"
        pending = _load_pending_consent(pending_store, nonce, user_id, resource, consume=True)
        client_id = pending["client_id"]
        category = resolve_client_category(client_id, settings.jwt_token_config)
        grant = (
            consent_store.grant_server_consent
            if resource == PendingConsentFlow.SERVER
            else consent_store.grant_agent_consent
        )
        if not grant(
            pending["user_id"], client_id, pending[path_field], require_app_consent=requires_app_consent(category)
        ):
            raise HTTPException(status_code=409, detail=_APP_REVOKED_DETAIL)

        return {"status": "ok", "client_branding": _client_branding_from_meta(pending)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to approve %s consent user_id=%s", resource, user_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post("/consent/server", responses=_APPROVAL_RESPONSES, openapi_extra=CONSENT_WRITE_OPENAPI)
async def approve_server_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    consent_store: ConsentStore = Depends(get_consent_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str | None]:
    return await asyncio.to_thread(
        _approve_resource_consent,
        PendingConsentFlow.SERVER,
        body.nonce,
        user_context["user_id"],
        pending_store,
        consent_store,
    )


@router.get("/consent/agent")
async def get_agent_consent_context(
    nonce: str,
    user_context: CurrentUser,
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    agent_service: A2AAgentService = Depends(get_a2a_agent_service),
) -> dict[str, str | int | None]:
    try:
        pending = await asyncio.to_thread(
            _load_pending_consent, pending_store, nonce, user_context["user_id"], PendingConsentFlow.AGENT
        )

        client_metadata = store.get_client(pending["client_id"]) or {}
        agent = await agent_service.get_agent_by_path(pending["agent_path"])
        agent_name = agent.config.title if agent and agent.config else agent.card.name if agent and agent.card else None
        return {
            "client_name": client_metadata.get("client_name", "Unknown application"),
            "client_uri": client_metadata.get("client_uri"),
            "ip_address": client_metadata.get("ip_address"),
            "registered_at": client_metadata.get("registered_at"),
            "agent_path": pending["agent_path"],
            "agent_name": agent_name or pending["agent_path"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Agent Consent] failed to fetch consent context: user={user_context['user_id']}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/agent", responses=_APPROVAL_RESPONSES, openapi_extra=CONSENT_WRITE_OPENAPI)
async def approve_agent_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    consent_store: ConsentStore = Depends(get_consent_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str | None]:
    return await asyncio.to_thread(
        _approve_resource_consent,
        PendingConsentFlow.AGENT,
        body.nonce,
        user_context["user_id"],
        pending_store,
        consent_store,
    )


async def _deny_consent(
    nonce: str,
    user_context: CurrentUser,
    pending_store: PendingConsentStore,
    *,
    flow: PendingConsentFlow,
    store: DownstreamOAuthStoreProtocol | None = None,
) -> dict[str, str | None]:
    """Deny a request belonging to the signed-in user and this consent page.

    The pending record is proactively removed and no consent is granted. Device-flow denials are
    additionally persisted on the device code so the polling client immediately receives
    ``access_denied``; other consent flows record nothing new and are gated again on retry.
    """
    try:
        pending = await asyncio.to_thread(
            _load_pending_consent, pending_store, nonce, user_context["user_id"], flow, consume=True
        )
        if pending.get("flow_type") == "device" and store is not None:
            if not mark_device_denied(pending["device_code"], store):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        client_branding = _client_branding_from_meta(pending)
        return {"status": "denied", "client_branding": client_branding}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to deny %s consent user_id=%s", flow, user_context["user_id"])
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/downstream/deny")
async def deny_downstream_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    store: DownstreamOAuthStoreProtocol = Depends(get_oauth_state_store),
) -> dict[str, str | None]:
    return await _deny_consent(
        body.nonce,
        user_context,
        pending_store,
        flow=PendingConsentFlow.DOWNSTREAM,
        store=store,
    )


@router.get("/consent/downstream-error")
async def get_downstream_error_consent_context(
    nonce: str,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str | None]:
    try:
        pending = _peek_downstream_error_consent(pending_store, nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        return {
            "redirect_uri": pending["redirect_uri"],
            "error": pending["error"],
            "error_description": pending["error_description"],
            "server_path": pending["server_path"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[Downstream Error Consent] failed to fetch consent context")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/downstream-error")
async def approve_downstream_error_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str]:
    try:
        pending = _peek_downstream_error_consent(pending_store, body.nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        consumed = _consume_downstream_error_consent(pending_store, body.nonce)
        if consumed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")

        redirect_url = build_oauth_error_redirect_url(
            consumed["redirect_uri"],
            consumed["error"],
            consumed["error_description"],
            consumed.get("client_state"),
        )
        return {"redirect_url": redirect_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[Downstream Error Consent] failed to approve")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/downstream-error/deny")
async def deny_downstream_error_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str]:
    try:
        pending = _peek_downstream_error_consent(pending_store, body.nonce)
        if pending is None or pending["user_id"] != user_context["user_id"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        if _consume_downstream_error_consent(pending_store, body.nonce) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This consent link has expired.")
        return {"status": "denied"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[Downstream Error Consent] failed to deny")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.post("/consent/server/deny")
async def deny_server_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str | None]:
    return await _deny_consent(body.nonce, user_context, pending_store, flow=PendingConsentFlow.SERVER)


@router.post("/consent/agent/deny")
async def deny_agent_consent(
    body: ApproveConsentRequest,
    user_context: CurrentUser,
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> dict[str, str | None]:
    return await _deny_consent(body.nonce, user_context, pending_store, flow=PendingConsentFlow.AGENT)
