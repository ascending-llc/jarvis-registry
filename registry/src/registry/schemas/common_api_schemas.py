"""
Pydantic Schemas for Common API Endpoints

These schemas define the request and response models for common endpoints
including auth, token, mcp connection, and oauth endpoints.

All schemas use camelCase for API input/output and for MongoDB storage,
following the same pattern as Server Management and A2A Agent APIs.
"""

from pydantic import Field

from registry.schemas.case_conversion import APIBaseModel

# ==================== Auth Schemas ====================


class UserInfoResponse(APIBaseModel):
    """Response schema for current user information"""

    username: str = Field(..., description="Username")
    authMethod: str = Field(default="basic", description="Authentication method")
    provider: str | None = Field(default=None, description="Auth provider")
    scopes: list[str] = Field(default_factory=list, description="User scopes")
    groups: list[str] = Field(default_factory=list, description="User groups")
    userId: str | None = Field(default=None, description="User ID")


# ==================== Token Schemas ====================


class TokenData(APIBaseModel):
    """Token data schema"""

    accessToken: str = Field(..., description="Access token")
    expiresIn: int = Field(..., description="Token expiration time in seconds")
    tokenType: str = Field(default="Bearer", description="Token type")
    scope: str = Field(default="", description="Token scope")


class TokenGenerateResponse(APIBaseModel):
    """Response schema for token generation"""

    success: bool = Field(default=True, description="Success flag")
    tokenData: TokenData = Field(..., description="Token data")
    userScopes: list[str] = Field(..., description="User scopes")
    requestedScopes: list[str] = Field(..., description="Requested scopes")


# ==================== MCP Connection Schemas ====================


class ServerConnectionStatusResponse(APIBaseModel):
    """Response schema for server connection status"""

    success: bool = Field(default=True, description="Success flag")
    serverName: str = Field(..., description="Server name")
    connectionState: str = Field(..., description="Connection state")
    requiresOAuth: bool = Field(..., description="Whether OAuth is required")
    serverId: str = Field(..., description="Server ID")


class ConnectionStatusMapResponse(APIBaseModel):
    """Response schema for all connection statuses"""

    success: bool = Field(default=True, description="Success flag")
    connectionStatus: dict[str, dict] = Field(..., description="Connection status map")


# ==================== OAuth Schemas ====================


class OAuthMetadataDiscoverResponse(APIBaseModel):
    """Response schema for OAuth metadata discovery"""

    serverUrl: str = Field(..., description="Server URL")
    metadata: dict | None = Field(default=None, description="OAuth metadata")
    message: str = Field(..., description="Result message")


class OAuthInitiateResponse(APIBaseModel):
    """Response schema for OAuth flow initiation"""

    flowId: str = Field(..., description="OAuth flow ID")
    authorizationUrl: str = Field(..., description="Authorization URL")
    serverId: str = Field(..., description="Server ID")
    userId: str = Field(..., description="User ID")
    serverName: str = Field(..., description="Server name")


class OAuthOperationResponse(APIBaseModel):
    """Response schema for OAuth operations (cancel, refresh, delete)"""

    success: bool = Field(default=True, description="Success flag")
    message: str = Field(..., description="Operation result message")
    serverId: str = Field(..., description="Server ID")
    userId: str = Field(..., description="User ID")
    serverName: str | None = Field(default=None, description="Server name")


class OAuthTokensResponse(APIBaseModel):
    """Response schema for OAuth tokens"""

    tokens: dict = Field(..., description="OAuth tokens")
