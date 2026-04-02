"""
Common error response schemas for API endpoints.

This module provides standardized error response models that can be reused
across all API endpoints to ensure consistent error handling.
"""

from typing import Any

from pydantic import BaseModel, Field


class APIErrorDetail(BaseModel):
    """
    Structured error detail with error code and message.

    Example:
        {
            "error": "authentication_required",
            "message": "Not authenticated"
        }
    """

    error: str = Field(
        ...,
        description="Machine-readable error code",
        examples=["authentication_required", "invalid_request", "service_unavailable"],
    )
    message: str = Field(
        ...,
        description="Human-readable error message with all details",
        examples=[
            "Not authenticated",
            "Invalid request: query parameter must be between 1 and 512 characters",
            "Service unavailable: FAISS search engine is temporarily offline",
        ],
    )


class APIErrorResponse(BaseModel):
    """
    Standard error response wrapper.

    This model wraps the APIErrorDetail to match FastAPI's HTTPException format.

    Example:
        {
            "detail": {
                "error": "authentication_required",
                "message": "Not authenticated"
            }
        }
    """

    detail: APIErrorDetail = Field(..., description="Error details including error code and message")


# Common error codes constants for consistency
class ErrorCode:
    """Common error codes used across the application."""

    # Authentication & Authorization (4xx)
    AUTHENTICATION_REQUIRED = "authentication_required"
    INVALID_CREDENTIALS = "invalid_credentials"
    INSUFFICIENT_PERMISSIONS = "insufficient_permissions"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_INVALID = "token_invalid"

    # Request Validation (4xx)
    INVALID_REQUEST = "invalid_request"
    INVALID_PARAMETER = "invalid_parameter"
    MISSING_PARAMETER = "missing_parameter"
    DUPLICATE_ENTRY = "duplicate_entry"
    RESOURCE_NOT_FOUND = "resource_not_found"

    # Server Errors (5xx)
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_ERROR = "internal_error"
    DATABASE_ERROR = "database_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    NOT_IMPLEMENTED = "not_implemented"

    # MCP Specific
    HEALTH_CHECK_FAILED = "health_check_failed"
    TOOL_RETRIEVAL_FAILED = "tool_retrieval_failed"
    SERVER_CONNECTION_FAILED = "server_connection_failed"
    OAUTH_ERROR = "oauth_error"

    CONFLICT = "conflict"
    NOT_FOUND = "not_found"


def create_error_detail(error_code: str, message: str) -> dict[str, Any]:
    """
    Helper function to create a structured error detail dictionary.

    This is useful when raising HTTPException with structured error details.

    Args:
        error_code: Machine-readable error code
        message: Human-readable error message (include all details here)

    Returns:
        Dictionary containing error and message

    Example:
        raise HTTPException(
            status_code=401,
            detail=create_error_detail(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "Not authenticated"
            )
        )

        raise HTTPException(
            status_code=400,
            detail=create_error_detail(
                ErrorCode.INVALID_PARAMETER,
                "Invalid URL format: URL must start with http:// or https://, got 'ftp://example.com'"
            )
        )
    """
    return {"error": error_code, "message": message}


# ========================================
# Authentication Exceptions
# ========================================


class AuthenticationError(Exception):
    """Base exception for authentication errors."""

    pass


class OAuthReAuthRequiredError(AuthenticationError):
    """
    OAuth re-authentication is required.

    Raised when:
    - Access token expired and refresh token is invalid/expired
    - User needs to go through OAuth flow again

    Attributes:
        auth_url: The OAuth authorization URL for re-authentication
        server_name: Name of the server requiring re-auth
    """

    def __init__(self, message: str, auth_url: str = None, server_name: str = None):
        super().__init__(message)
        self.auth_url = auth_url
        self.server_name = server_name


class OAuthTokenError(AuthenticationError):
    """
    OAuth token operation failed.

    Raised when:
    - Token refresh failed
    - Token validation failed
    - Token service error

    Attributes:
        server_name: Name of the server with token error
        original_error: Original exception if available
    """

    def __init__(self, message: str, server_name: str = None, original_error: Exception = None):
        super().__init__(message)
        self.server_name = server_name
        self.original_error = original_error


class MissingUserIdError(AuthenticationError):
    """
    User ID is required but not provided.

    Raised when:
    - OAuth server requires user_id for token retrieval
    - User context is missing

    Attributes:
        server_name: Name of the server requiring user_id
    """

    def __init__(self, message: str, server_name: str = None):
        super().__init__(message)
        self.server_name = server_name


class ApiKeyError(AuthenticationError):
    """
    API key authentication error.

    Raised when:
    - API key is invalid or malformed
    - API key configuration is incorrect

    Attributes:
        server_name: Name of the server with API key error
    """

    def __init__(self, message: str, server_name: str = None):
        super().__init__(message)
        self.server_name = server_name
