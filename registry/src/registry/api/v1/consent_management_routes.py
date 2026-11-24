import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Response, status

from registry_pkgs.core.consent_store import ConsentEntry

from ...auth.dependencies import CurrentUser, UserContextDict
from ...constants import OPENAPI_SESSION_COOKIE_SCHEME
from ...deps import get_consent_management_service
from ...schemas.consent_api_schemas import (
    ClientRevokeResponse,
    ConsentClientListResponse,
    ConsentErrorResponse,
    ResourceRevokeResponse,
    RevokeAllClients,
    RevokeResourcesRequest,
    RevokeSelectedClients,
)
from ...services.consent_management_service import ConsentManagementService
from .consent_openapi import CONSENT_WRITE_ERRORS, CONSENT_WRITE_OPENAPI

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/consents", tags=["consent-management"])

_REVOKE_RESPONSES = {
    **CONSENT_WRITE_ERRORS,
    400: {"model": ConsentErrorResponse, "description": "The requested revocation is invalid."},
    500: {"model": ConsentErrorResponse, "description": "Revocation could not be completed."},
}


def _session_user_id(user_context: UserContextDict) -> str:
    user_id = user_context["user_id"]
    if not user_id:
        raise HTTPException(status_code=403, detail="User ID required")
    return user_id


@contextmanager
def _revoke_errors() -> Iterator[None]:
    try:
        yield
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to revoke consent")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.get(
    "/clients",
    responses={
        401: {"model": ConsentErrorResponse, "description": "A valid Registry session cookie is required."},
        403: {"model": ConsentErrorResponse, "description": "The signed-in user lacks the user-read scope."},
        500: {"model": ConsentErrorResponse, "description": "The consent list could not be loaded."},
    },
    openapi_extra={"security": [{OPENAPI_SESSION_COOKIE_SCHEME: []}]},
)
async def list_consent_clients(
    user_context: CurrentUser,
    service: ConsentManagementService = Depends(get_consent_management_service),
) -> ConsentClientListResponse:
    """List the signed-in user's app and resource approvals."""
    user_id = _session_user_id(user_context)
    try:
        return await service.list_clients(user_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list consent clients")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.delete(
    "/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_REVOKE_RESPONSES,
    openapi_extra=CONSENT_WRITE_OPENAPI,
)
async def revoke_consent_client(
    client_id: str,
    user_context: CurrentUser,
    service: ConsentManagementService = Depends(get_consent_management_service),
) -> Response:
    """Revoke the signed-in user's consent to one app across all devices."""
    with _revoke_errors():
        await service.revoke_client(_session_user_id(user_context), client_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/clients/revoke", responses=_REVOKE_RESPONSES, openapi_extra=CONSENT_WRITE_OPENAPI)
async def revoke_consent_clients(
    body: RevokeSelectedClients | RevokeAllClients,
    user_context: CurrentUser,
    service: ConsentManagementService = Depends(get_consent_management_service),
) -> ClientRevokeResponse:
    """Revoke the signed-in user's consent to selected apps or all revocable apps."""
    client_ids = body.clientIds if isinstance(body, RevokeSelectedClients) else None
    with _revoke_errors():
        results = await service.revoke_clients(
            _session_user_id(user_context), client_ids, revoke_all=client_ids is None
        )
    return ClientRevokeResponse(results=results)


@router.post("/clients/{client_id}/resources/revoke", responses=_REVOKE_RESPONSES, openapi_extra=CONSENT_WRITE_OPENAPI)
async def revoke_consent_resources(
    client_id: str,
    body: RevokeResourcesRequest,
    user_context: CurrentUser,
    service: ConsentManagementService = Depends(get_consent_management_service),
) -> ResourceRevokeResponse:
    """Revoke a signed-in user's approvals for individual MCP servers or A2A agents."""
    entries = [ConsentEntry(resource.type, resource.path) for resource in body.resources]
    with _revoke_errors():
        results = await service.revoke_resources(_session_user_id(user_context), client_id, entries)
    return ResourceRevokeResponse(results=results)
