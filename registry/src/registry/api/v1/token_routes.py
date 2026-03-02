import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from ...auth.dependencies import CurrentUser
from ...core.config import settings
from ...utils.keycloak_manager import KEYCLOAK_ADMIN_URL, KEYCLOAK_REALM

logger = logging.getLogger(__name__)

router = APIRouter()


class RatingRequest(BaseModel):
    rating: int


@router.post("/tokens/generate")
async def generate_user_token(
    request: Request,
    user_context: CurrentUser,
):
    """
    Generate a JWT token for the authenticated user.

    Request body should contain:
    {
        "requested_scopes": ["scope1", "scope2"],  // Optional, defaults to user's current scopes
        "expires_in_hours": 8,                     // Optional, must be one of: 1, 8, 24
        "description": "Token for automation"      // Optional description
    }

    Returns:
        Generated JWT token with expiration info (no refresh token)

    Raises:
        HTTPException: If request fails or user lacks permissions
    """

    try:
        # Parse request body
        try:
            body = await request.json()
        except Exception as e:
            logger.warning(f"Invalid JSON in token generation request: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON in request body")

        requested_scopes = body.get("requested_scopes", [])
        expires_in_hours = body.get("expires_in_hours", 8)
        description = body.get("description", "")

        # Validate expires_in_hours - only allow 1, 8, or 24 hours
        allowed_hours = [1, 8, 24]
        if expires_in_hours not in allowed_hours:
            raise HTTPException(
                status_code=400,
                detail=f"expires_in_hours must be one of: {allowed_hours} (1hr, 8hr, or 24hr)",
            )

        # Validate requested_scopes
        if requested_scopes and not isinstance(requested_scopes, list):
            raise HTTPException(status_code=400, detail="requested_scopes must be a list of strings")

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

                # Format response - remove tokens wrapper and refresh_token
                formatted_response = {
                    "success": True,
                    "token_data": {
                        "access_token": token_data.get("access_token"),
                        # Note: refresh_token is intentionally excluded for API tokens
                        "expires_in": token_data.get("expires_in"),
                        "token_type": token_data.get("token_type", "Bearer"),
                        "scope": token_data.get("scope", ""),
                    },
                    "user_scopes": user_context["scopes"],
                    "requested_scopes": requested_scopes or user_context["scopes"],
                }

                return formatted_response
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
    except Exception as e:
        logger.error(f"Unexpected error generating token for user '{user_context['username']}': {e}")
        raise HTTPException(status_code=500, detail="Internal error generating token")


@router.get("/admin/tokens")
async def get_admin_tokens(
    user_context: CurrentUser,
):
    """
    Admin-only endpoint to retrieve JWT tokens from Keycloak.

    Returns both access token and refresh token for admin users.

    Returns:
        JSON object containing access_token, refresh_token, expires_in, etc.

    Raises:
        HTTPException: If user is not admin or token retrieval fails
    """
    # Check if user is admin
    if not user_context.get("is_admin", False):
        logger.warning(f"Non-admin user {user_context['username']} attempted to access admin tokens")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only available to admin users",
        )

    try:
        # Get M2M client credentials from environment
        m2m_client_id = os.getenv("KEYCLOAK_M2M_CLIENT_ID", "mcp-gateway-m2m")
        m2m_client_secret = os.getenv("KEYCLOAK_M2M_CLIENT_SECRET")

        if not m2m_client_secret:
            raise HTTPException(status_code=500, detail="Keycloak M2M client secret not configured")

        # Get tokens from Keycloak mcp-gateway realm using M2M client_credentials
        token_url = f"{KEYCLOAK_ADMIN_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"

        data = {
            "grant_type": "client_credentials",
            "client_id": m2m_client_id,
            "client_secret": m2m_client_secret,
        }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(token_url, data=data, headers=headers)
            response.raise_for_status()

            token_data = response.json()

            # No refresh tokens - users should configure longer token lifetimes in Keycloak if needed
            refresh_token = None
            refresh_expires_in_seconds = 0

            logger.info(
                f"Admin user {user_context['username']} retrieved Keycloak M2M tokens (no refresh token - configure token lifetime in Keycloak if needed)"
            )

            return {
                "success": True,
                "tokens": {
                    "access_token": token_data.get("access_token"),
                    "refresh_token": refresh_token,  # Custom-generated refresh token
                    "expires_in": token_data.get("expires_in"),
                    "refresh_expires_in": refresh_expires_in_seconds,
                    "token_type": token_data.get("token_type", "Bearer"),
                    "scope": token_data.get("scope", ""),
                },
                "keycloak_url": KEYCLOAK_ADMIN_URL,
                "realm": KEYCLOAK_REALM,
                "client_id": m2m_client_id,
            }

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to retrieve Keycloak tokens: HTTP {e.response.status_code}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to authenticate with Keycloak: HTTP {e.response.status_code}",
        )
    except Exception as e:
        logger.error(f"Unexpected error retrieving Keycloak tokens: {e}")
        raise HTTPException(status_code=500, detail="Internal error retrieving Keycloak tokens")
