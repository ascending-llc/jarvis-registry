from fastapi import APIRouter

from ..auth.dependencies import CurrentUser
from ..core.config import settings
from ..schemas.common_api_schemas import UserInfoResponse
from ..services.generated_token_policy import get_user_token_scopes

router = APIRouter()


@router.get("/api/auth/me", response_model=UserInfoResponse, response_model_by_alias=True)
async def get_current_user(user_context: CurrentUser) -> UserInfoResponse:
    """Get current user information for React auth context."""
    return UserInfoResponse(
        username=user_context.get("username"),
        authMethod=user_context.get("auth_method", "basic"),
        provider=user_context.get("provider"),
        scopes=user_context.get("scopes", []),
        groups=user_context.get("groups", []),
        userId=user_context.get("user_id"),
        tokenScopes=get_user_token_scopes(user_context.get("groups", [])),
    )


@router.get("/health")
async def health_check():
    """Simple health check for load balancers and monitoring."""
    return {"status": "healthy", "service": "mcp-gateway-registry"}


@router.get("/api/version")
async def get_version():
    """Return the build version exposed to the frontend and diagnostics endpoints."""
    return {"version": settings.build_version}
