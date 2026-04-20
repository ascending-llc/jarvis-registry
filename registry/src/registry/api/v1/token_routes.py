import logging
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt

from ...auth.dependencies import CurrentUser
from ...core.config import settings
from ...schemas.common_api_schemas import TokenData, TokenGenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter()

KEYCLOAK_ADMIN_URL = settings.keycloak_url
KEYCLOAK_REALM = settings.keycloak_realm

# Simple in-memory rate limiting counter for token generation
user_token_generation_counts = {}
MAX_TOKENS_PER_USER_PER_HOUR = getattr(settings, "max_tokens_per_user_per_hour", 50)
MAX_TOKEN_LIFETIME_HOURS = getattr(settings, "max_token_lifetime_hours", 24)


def check_rate_limit(username: str) -> bool:
    """
    Check if user has exceeded the rate limit for token generation.
    Returns True if within rate limit, False if exceeded.
    """
    current_time = int(time.time())
    hour_ago = current_time - 3600

    # Clean up old entries
    expired_keys = [key for key, timestamp in user_token_generation_counts.items() if timestamp < hour_ago]
    for key in expired_keys:
        del user_token_generation_counts[key]

    # Count tokens generated in the last hour
    user_key_prefix = f"{username}:"
    recent_count = sum(
        1
        for key, timestamp in user_token_generation_counts.items()
        if key.startswith(user_key_prefix) and timestamp >= hour_ago
    )

    if recent_count >= MAX_TOKENS_PER_USER_PER_HOUR:
        return False

    # Record this token generation
    user_token_generation_counts[f"{username}:{current_time}"] = current_time
    return True


class RatingRequest(BaseModel):
    rating: int


@router.post("/tokens/generate", response_model=TokenGenerateResponse, response_model_by_alias=True)
async def generate_user_token(
    request: Request,
    user_context: CurrentUser,
) -> TokenGenerateResponse:
    """
    Generate a JWT token for the authenticated user.

    Request body should contain:
    {
        "requested_scopes": ["scope1", "scope2"],  // Optional, defaults to user's current scopes
        "expires_in_hours": 8,                     // Optional, must be between 1 and MAX_TOKEN_LIFETIME_HOURS
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

        # Extract user information
        username = user_context.get("username")
        user_scopes = user_context.get("scopes", [])
        user_groups = user_context.get("groups", [])
        user_id = user_context.get("user_id")

        if not username:
            raise HTTPException(status_code=400, detail="Username is required in user context")

        # Check rate limit
        if not check_rate_limit(username):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Maximum {MAX_TOKENS_PER_USER_PER_HOUR} tokens per hour.",
            )

        # Validate expires_in_hours
        if expires_in_hours <= 0 or expires_in_hours > MAX_TOKEN_LIFETIME_HOURS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid expiration time. Must be between 1 and {MAX_TOKEN_LIFETIME_HOURS} hours.",
            )

        # Validate requested_scopes
        if requested_scopes and not isinstance(requested_scopes, list):
            raise HTTPException(status_code=400, detail="requested_scopes must be a list of strings")

        # Use requested scopes or default to user scopes
        final_scopes = requested_scopes if requested_scopes else user_scopes

        # Check if requested scopes are within user's current scopes
        if requested_scopes:
            user_scopes_set = set(user_scopes)
            requested_scopes_set = set(requested_scopes)

            invalid_scopes = requested_scopes_set - user_scopes_set
            if invalid_scopes:
                logger.warning(f"User '{username}' requested scopes not in their permission: {invalid_scopes}")
                raise HTTPException(
                    status_code=403,
                    detail=f"Requested scopes exceed user permissions. Invalid scopes: {list(invalid_scopes)}",
                )

        # Generate JWT token locally (moved from auth-server)
        current_time = int(time.time())
        expires_in_seconds = expires_in_hours * 3600

        extra_claims = {
            "user_id": user_id,
            "scope": " ".join(final_scopes),
            "groups": user_groups,
            "jti": str(uuid.uuid4()),
            "token_use": "access",
            "client_id": "user-generated",
        }

        if description:
            extra_claims["description"] = description

        access_payload = build_jwt_payload(
            subject=username,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            expires_in_seconds=expires_in_seconds,
            iat=current_time,
            extra_claims=extra_claims,
        )

        access_token = encode_jwt(access_payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)

        logger.info(f"Successfully generated token for user '{username}' with expiry {expires_in_hours}h")

        # Format response using Pydantic schema
        return TokenGenerateResponse(
            success=True,
            tokenData=TokenData(
                accessToken=access_token,
                expiresIn=expires_in_seconds,
                tokenType="Bearer",
                scope=" ".join(final_scopes),
            ),
            userScopes=user_scopes,
            requestedScopes=final_scopes,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error generating token for user '{user_context.get('username', 'unknown')}': {e}")
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
        m2m_client_id = settings.keycloak_m2m_client_id
        m2m_client_secret = settings.keycloak_m2m_client_secret

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
