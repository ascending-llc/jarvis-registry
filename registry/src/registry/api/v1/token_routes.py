import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from registry_pkgs.core.jwt_tokens import mint_managed_agent_token_with_scope

from ...auth.dependencies import CurrentUser
from ...core.config import settings
from ...schemas.common_api_schemas import TokenData, TokenGenerateRequest, TokenGenerateResponse
from ...services.generated_token_policy import (
    get_user_token_scopes,
    resolve_generated_token_client_id,
    resolve_generated_token_scopes,
)

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
        "requestedScopes": ["scope1", "scope2"],   // Optional, defaults to the user's token-eligible scopes
        "tokenPurpose": "interactive"              // Optional: "interactive" or "agent"
    }

    Returns:
        Generated JWT token with expiration info (no refresh token)

    Raises:
        HTTPException: If request fails or user lacks permissions
    """

    try:
        expires_in_hours = request_data.expiresInHours
        description = request_data.description
        token_purpose = request_data.tokenPurpose

        # Extract user information
        username = user_context.get("username")
        user_groups = user_context.get("groups", [])
        user_id = user_context.get("user_id")

        if not username:
            raise HTTPException(status_code=400, detail="Username is required in user context")

        available_scopes = get_user_token_scopes(user_groups)
        requested_scopes = (
            request_data.requestedScopes if request_data.requestedScopes is not None else available_scopes
        )
        final_scopes = resolve_generated_token_scopes(
            request_data.requestedScopes,
            available_scopes,
            token_purpose,
            settings.jwt_token_config,
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
        client_id = resolve_generated_token_client_id(
            token_purpose,
            settings.headless_agent_client_id,
        )
        minted = mint_managed_agent_token_with_scope(
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
                accessToken=minted.token,
                expiresIn=expires_in_seconds,
                tokenType="Bearer",
                scope=minted.scope,
            ),
            userScopes=available_scopes,
            requestedScopes=requested_scopes,
        )

    except HTTPException:
        raise
    except ValidationError as e:
        logger.warning(f"Validation error in token generation request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error generating token for user '{user_context['username']}': {e}")
        raise HTTPException(status_code=500, detail="Internal error generating token")
