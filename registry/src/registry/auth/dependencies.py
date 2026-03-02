import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import URLSafeTimedSerializer

from auth_utils.models import UserContextDict
from registry_pkgs.core.scopes import load_scopes_config

from ..core.config import settings

logger = logging.getLogger(__name__)

signer = URLSafeTimedSerializer(settings.secret_key)


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
