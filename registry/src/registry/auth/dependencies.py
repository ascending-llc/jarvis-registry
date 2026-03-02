import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import URLSafeTimedSerializer

from registry_pkgs.core.scopes import load_scopes_config

from ..core.config import settings

logger = logging.getLogger(__name__)

signer = URLSafeTimedSerializer(settings.secret_key)


def get_current_user(request: Request) -> dict[str, Any]:
    """
        Get current authenticated user from request state
        This function replaces the need for Depends(enhanced_auth) or
    Depends(nginx_proxied_auth) in route handlers.

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
# FastAPI extracts the `user` attribute (typed as dict[str, Any]) of the current request and pass it to the parameter.
# Since it's Python 3.12, we use the new type statement instead of typing.TypeAlias
type CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]

# Global scopes configuration loaded from centralized loader
SCOPES_CONFIG = load_scopes_config()


def map_cognito_groups_to_scopes(groups: list[str]) -> list[str]:
    """
    Map Cognito groups to MCP scopes using the scopes.yml configuration.

    Args:
        groups: List of Cognito group names

    Returns:
        List of MCP scopes
    """
    scopes = []
    group_mappings = SCOPES_CONFIG.get("group_mappings", {})

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
