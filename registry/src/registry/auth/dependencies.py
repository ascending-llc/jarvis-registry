import logging
from typing import Annotated, Any, TypedDict

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import URLSafeTimedSerializer

from ..core.config import settings

logger = logging.getLogger(__name__)

signer = URLSafeTimedSerializer(settings.secret_key)


class UserContextDict(TypedDict):
    """
    UserContextDict is the type of the dictionary set as the Request.state.user attribute for each incoming request
    **to a non-public route** by the UnifiedAuthMiddlware.
    If a FastAPI path operation function needs to access this UserContextDict information, it can retrieve this dictionary
    by asking for the `user_context: CurrentUser` dependency injection.
    If an MCP tool/resource/prompt handler function needs it, it can retrieve this dictionary by asking for the
    `ctx: Context` dependency injection (`mcp.server.fastmcp.Context`) and then accessing
    `ctx.request_context.request.state.user`.
    """

    # From the "user_id" field of the JWT claim.
    user_id: str | None

    # From the "sub" field of the JWT claim.
    username: str | None

    # From the "groups" field of the JWT claim.
    groups: list[str]

    # Converted from the "scope" field of the JWT claim, or mapped from the "groups" field if "scope" doesn't exist.
    scopes: list[str]

    # "jwt" if the JWT token comes from the Authorization header. "traditional" if from browser cookie.
    auth_method: str

    # "jwt" if the JWT token comes from the Authorization header. "local" if from browser cookie.
    provider: str

    # "jwt_auth" if the JWT token comes from the Authorization header. "jwt_session_auth" if from browser cookie.
    auth_source: str


def get_current_user(request: Request) -> UserContextDict:
    """
    Get current authenticated user from request state.

    Args:
        request: FastAPI request object

    Returns:
        User context dictionary with all authentication details

    Raises:
        HTTPException: If user is not authenticated
    """
    if not hasattr(request.state, "user") or not request.state.is_authenticated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Is not authenticated")
    return request.state.user


# Use this type to annotate a parameter of a path operation function or its dependency function so that
# FastAPI extracts the `user` attribute (typed as UserContextDict) of the current request and pass it to the parameter.
# Since it's Python 3.12, we use the new type statement instead of typing.TypeAlias
type CurrentUser = Annotated[UserContextDict, Depends(get_current_user)]
# Global scopes configuration loaded from centralized loader
SCOPES_CONFIG = settings.scopes_config


def map_cognito_groups_to_scopes(groups: list[str]) -> list[str]:
    """
    Map Cognito groups to MCP scopes using the scopes.yml configuration.

    Args:
        groups: List of Cognito group names

    Returns:
        List of MCP scopes
    """
    scopes = []
    group_mappings = settings.scopes_config.get("group_mappings", {})

    for group in groups:
        if group in group_mappings:
            group_scopes = group_mappings[group]
            scopes.extend(group_scopes)
            logger.debug(f"Mapped group '{group}' to scopes: {group_scopes}")
        else:
            logger.debug(f"No scope mapping found for group: {group}")

    # Remove duplicates while preserving order
    seen = set()
    unique_scopes = []
    for scope in scopes:
        if scope not in seen:
            seen.add(scope)
            unique_scopes.append(scope)

    logger.info(f"Final mapped scopes: {unique_scopes}")
    return unique_scopes


def effective_scopes_from_context(user_context: dict[str, Any]) -> list[str]:
    """
    Determine the effective scopes for a user based on the authentication context.

    Explicit scopes (from the token's `scope` claim) take precedence. If any explicit
    scopes are present, they are returned as-is (de-duplicated, preserving order)
    without augmentation from group-mapped scopes. This avoids unintentionally
    broadening permissions for down-scoped tokens.
    If no explicit scopes are present, scopes are derived solely from group mappings.
    """
    explicit_scopes = list(user_context.get("scopes") or [])
    if explicit_scopes:
        seen: set[str] = set()
        unique_scopes: list[str] = []
        for scope in explicit_scopes:
            if scope not in seen:
                seen.add(scope)
                unique_scopes.append(scope)
        return unique_scopes

    groups = user_context.get("groups") or []
    if not groups:
        return []

    return map_cognito_groups_to_scopes(groups)
