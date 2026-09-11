"""Models for the registry service."""

from registry_pkgs.oauth.schemas import (
    MCPOAuthFlowMetadata,
    OAuthClientInformation,
    OAuthFlow,
    OAuthMetadata,
    OAuthProtectedResourceMetadata,
    OAuthTokens,
)

from .errors import (
    APIErrorDetail,
    APIErrorResponse,
    ErrorCode,
    create_error_detail,
)

__all__ = [
    # Error handling
    "APIErrorDetail",
    "APIErrorResponse",
    "ErrorCode",
    "create_error_detail",
    "OAuthTokens",
    "OAuthClientInformation",
    "OAuthMetadata",
    "OAuthProtectedResourceMetadata",
    "MCPOAuthFlowMetadata",
    "OAuthFlow",
]
