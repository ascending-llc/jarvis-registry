import logging

from fastapi import APIRouter, Depends, HTTPException

from ...auth.dependencies import CurrentUser
from ...constants import OPENAPI_SESSION_COOKIE_SCHEME
from ...deps import get_consent_management_service
from ...schemas.consent_api_schemas import ConsentClientListResponse
from ...services.consent_management_service import ConsentManagementService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/consents", tags=["consent-management"])


@router.get("/clients", openapi_extra={"security": [{OPENAPI_SESSION_COOKIE_SCHEME: []}]})
async def list_consent_clients(
    user_context: CurrentUser,
    service: ConsentManagementService = Depends(get_consent_management_service),
) -> ConsentClientListResponse:
    """List the signed-in user's app and resource approvals."""
    user_id = user_context["user_id"]
    if not user_id:
        raise HTTPException(status_code=403, detail="User ID required")
    try:
        return await service.list_clients(user_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list consent clients")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
