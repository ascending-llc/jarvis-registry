"""Internal-only routes for the auth server.

Endpoints are mounted under the API prefix by the main app, for example
`/api_prefix/internal/tokens`.
"""

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException

from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt

from ..core.config import settings
from ..models import GenerateTokenRequest, GenerateTokenResponse
from ..utils.security_mask import hash_username

logger = logging.getLogger(__name__)

router = APIRouter()

# Simple in-memory rate limiting counter for token generation
user_token_generation_counts = {}
MAX_TOKENS_PER_USER_PER_HOUR = settings.max_tokens_per_user_per_hour


def validate_scope_subset(user_scopes: list[str], requested_scopes: list[str]) -> bool:
    if not requested_scopes:
        return True
    return set(requested_scopes).issubset(set(user_scopes))


def check_rate_limit(username: str) -> bool:
    current_time = int(time.time())
    current_hour = current_time // 3600
    # Cleanup old keys
    for key in list(user_token_generation_counts.keys()):
        stored_hour = int(key.split(":")[1])
        if current_hour - stored_hour > 1:
            del user_token_generation_counts[key]

    rate_key = f"{username}:{current_hour}"
    current_count = user_token_generation_counts.get(rate_key, 0)
    if current_count >= MAX_TOKENS_PER_USER_PER_HOUR:
        logger.warning(f"Rate limit exceeded for user {hash_username(username)}: {current_count} tokens this hour")
        return False
    user_token_generation_counts[rate_key] = current_count + 1
    return True


@router.post("/internal/tokens", response_model=GenerateTokenResponse)
async def generate_user_token(request: GenerateTokenRequest):
    try:
        user_context = request.user_context
        username = user_context.get("username")
        user_scopes = user_context.get("scopes", [])
        user_groups = user_context.get("groups", [])
        user_id = user_context.get("user_id")

        if not username:
            raise HTTPException(status_code=400, detail="Username is required in user context")

        if not check_rate_limit(username):
            raise HTTPException(
                status_code=429, detail=f"Rate limit exceeded. Maximum {MAX_TOKENS_PER_USER_PER_HOUR} tokens per hour."
            )

        expires_in_hours = request.expires_in_hours
        if expires_in_hours <= 0 or expires_in_hours > settings.max_token_lifetime_hours:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid expiration time. Must be between 1 and {settings.max_token_lifetime_hours} hours.",
            )

        requested_scopes = request.requested_scopes if request.requested_scopes else user_scopes
        if not validate_scope_subset(user_scopes, requested_scopes):
            invalid_scopes = set(requested_scopes) - set(user_scopes)
            raise HTTPException(
                status_code=403,
                detail=f"Requested scopes exceed user permissions. Invalid scopes: {list(invalid_scopes)}",
            )

        current_time = int(time.time())
        expires_in_seconds = expires_in_hours * 3600

        extra_claims = {
            "user_id": user_id,
            "scope": " ".join(requested_scopes),
            "groups": user_groups,
            "jti": str(uuid.uuid4()),
            "token_use": "access",
            "client_id": "user-generated",
            "token_type": "user_generated",
        }

        if request.description:
            extra_claims["description"] = request.description

        access_payload = build_jwt_payload(
            subject=username,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            expires_in_seconds=expires_in_seconds,
            iat=current_time,
            extra_claims=extra_claims,
        )

        access_token = encode_jwt(access_payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)

        return GenerateTokenResponse(
            access_token=access_token,
            refresh_token=None,
            expires_in=expires_in_hours * 3600,
            refresh_expires_in=0,
            scope=" ".join(requested_scopes),
            issued_at=current_time,
            description=request.description,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error generating token: {e}")
        raise HTTPException(status_code=500, detail="Internal error generating token")
