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
        auth_url: The OAuth authorization URL for re-authentication (None for
            non-interactive callers, where no flow is initiated)
        server_name: Name of the server requiring re-auth
    """

    def __init__(self, message: str, auth_url: str | None = None, server_name: str | None = None):
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

    def __init__(self, message: str, server_name: str | None = None, original_error: Exception | None = None):
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

    def __init__(self, message: str, server_name: str | None = None):
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

    def __init__(self, message: str, server_name: str | None = None):
        super().__init__(message)
        self.server_name = server_name
