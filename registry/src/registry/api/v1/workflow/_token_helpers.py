from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi import status as http_status

from registry.auth.dependencies import UserContextDict
from registry.schemas.errors import ErrorCode, create_error_detail
from registry.utils.crypto_utils import generate_service_jwt


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.removeprefix("Bearer ").strip()


def build_registry_token(request: Request, user_context: UserContextDict) -> str:
    """Return a registry token for the runner.

    Priority:
    1. ``Authorization: Bearer <token>`` header (user OAuth token forwarded from client).
    2. A short-lived service JWT minted from the current user context — used when
       the client authenticates via cookie session (no Bearer header present).

    Raises:
        HTTPException(401): If neither source yields a usable token (user_id missing).
    """
    header_token = _extract_bearer_token(request)
    if header_token:
        return header_token

    user_id = user_context.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail=create_error_detail(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "Authenticated user context is missing user_id",
            ),
        )

    return generate_service_jwt(
        user_id=user_id,
        username=user_context.get("username"),
        scopes=user_context.get("scopes", []),
    )
