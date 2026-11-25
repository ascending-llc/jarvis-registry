"""Shared OpenAPI declarations for session-authenticated consent writes."""

from ...constants import OPENAPI_SESSION_COOKIE_SCHEME
from ...core.config import settings
from ...schemas.consent_api_schemas import ConsentErrorResponse

CONSENT_WRITE_OPENAPI = {
    "security": [{OPENAPI_SESSION_COOKIE_SCHEME: []}],
    "parameters": [
        {
            "in": "header",
            "name": settings.csrf_header_name,
            "required": True,
            "schema": {"type": "string"},
            "description": "CSRF token for the current Registry session.",
        }
    ],
}

CONSENT_WRITE_ERRORS = {
    401: {"model": ConsentErrorResponse, "description": "A valid Registry session cookie is required."},
    403: {"model": ConsentErrorResponse, "description": "A valid CSRF header and the user-read scope are required."},
    422: {"model": ConsentErrorResponse, "description": "Request validation failed."},
}
