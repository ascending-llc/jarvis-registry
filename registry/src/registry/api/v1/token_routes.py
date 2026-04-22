import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ...auth.dependencies import CurrentUser
from ...core.config import settings
from ...schemas.common_api_schemas import TokenData, TokenGenerateRequest, TokenGenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/tokens/generate", response_model=TokenGenerateResponse, response_model_by_alias=True)
async def generate_user_token(
    request_data: TokenGenerateRequest,
    user_context: CurrentUser,
) -> TokenGenerateResponse:
    """
    Generate a JWT token for the authenticated user.

    Request body should contain:
    {
        "expiresInHours": 8,                       // Required, must be one of: 1, 8, or 24
        "description": "Token for automation",     // Optional description
        "requestedScopes": ["scope1", "scope2"]    // Optional, defaults to user's current scopes
    }

    Returns:
        Generated JWT token with expiration info (no refresh token)

    Raises:
        HTTPException: If request fails or user lacks permissions
    """

    try:
        requested_scopes = request_data.requestedScopes or []
        expires_in_hours = request_data.expiresInHours
        description = request_data.description

        # Check if requested scopes are within user's current scopes
        if requested_scopes:
            user_scopes = set(user_context.get("scopes", []))
            requested_scopes_set = set(requested_scopes)

            # Check if all requested scopes are in user's current scopes
            invalid_scopes = requested_scopes_set - user_scopes
            if invalid_scopes:
                logger.warning(
                    f"User '{user_context['username']}' requested scopes not in their permission: {invalid_scopes}"
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot request scopes not in your current permissions. Invalid scopes: {list(invalid_scopes)}",
                )

        # Prepare request to auth server
        auth_request = {
            "user_context": {
                "username": user_context["username"],
                "scopes": user_context["scopes"],
                "groups": user_context["groups"],
                "user_id": user_context["user_id"],
            },
            "requested_scopes": requested_scopes,
            "expires_in_hours": expires_in_hours,
            "description": description,
        }

        # Call auth server internal API (no authentication needed since both are trusted internal services)
        async with httpx.AsyncClient() as client:
            headers = {"Content-Type": "application/json"}

            auth_server_url = settings.auth_server_url
            response = await client.post(
                f"{auth_server_url}/internal/tokens",
                json=auth_request,
                headers=headers,
                timeout=10.0,
            )

            if response.status_code == 200:
                token_data = response.json()
                logger.info(
                    f"Successfully generated token for user '{user_context['username']}' with expiry {expires_in_hours}h"
                )

                # Format response using Pydantic schema
                return TokenGenerateResponse(
                    success=True,
                    tokenData=TokenData(
                        accessToken=token_data.get("access_token"),
                        expiresIn=token_data.get("expires_in"),
                        tokenType=token_data.get("token_type", "Bearer"),
                        scope=token_data.get("scope", ""),
                    ),
                    userScopes=user_context["scopes"],
                    requestedScopes=requested_scopes or user_context["scopes"],
                )
            else:
                error_detail = "Unknown error"
                try:
                    error_response = response.json()
                    error_detail = error_response.get("detail", "Unknown error")
                except:
                    error_detail = response.text

                logger.warning(f"Auth server returned error {response.status_code}: {error_detail}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Token generation failed: {error_detail}",
                )

    except HTTPException:
        raise
    except ValidationError as e:
        logger.warning(f"Validation error in token generation request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error generating token for user '{user_context['username']}': {e}")
        raise HTTPException(status_code=500, detail="Internal error generating token")
