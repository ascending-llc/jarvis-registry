import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

from ...auth.dependencies import CurrentUser
from ...core.config import settings
from ...schemas.common_api_schemas import TokenData, TokenGenerateRequest, TokenGenerateResponse
from ...services.generated_token_policy import resolve_generated_token_client_id

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
        "requestedScopes": ["scope1", "scope2"],   // Optional, defaults to user's current scopes
        "tokenPurpose": "interactive"              // Optional: "interactive" or "agent"
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

        # Extract user information
        username = user_context.get("username")
        user_scopes = user_context.get("scopes", [])
        user_groups = user_context.get("groups", [])
        user_id = user_context.get("user_id")

        if not username:
            raise HTTPException(status_code=400, detail="Username is required in user context")

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
            "groups": user_groups,
            "jti": str(uuid.uuid4()),
            "token_use": "access",
        }

        if description:
            extra_claims["description"] = description

        # User-vended tokens are managed-agent (proxy / Bearer) class. The client ID records
        # whether this token may require an interactive per-server consent step.
        token_purpose = request_data.tokenPurpose
        client_id = resolve_generated_token_client_id(
            token_purpose,
            settings.headless_agent_client_id,
        )
        access_token = mint_managed_agent_token(
            settings.jwt_token_config,
            subject=username,
            client_id=client_id,
            requested_scopes=final_scopes,
            expires_in_seconds=expires_in_seconds,
            iat=current_time,
            extra_claims=extra_claims,
        )

        logger.info(
            "Successfully generated token for user '%s' with expiry %sh (purpose=%s, client_id=%s)",
            username,
            expires_in_hours,
            token_purpose.value,
            client_id,
        )

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
    except ValidationError as e:
        logger.warning(f"Validation error in token generation request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error generating token for user '{user_context['username']}': {e}")
        raise HTTPException(status_code=500, detail="Internal error generating token")
