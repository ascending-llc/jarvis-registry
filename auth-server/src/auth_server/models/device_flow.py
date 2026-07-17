"""
Pydantic models for OAuth 2.0 Device Flow.
"""

from pydantic import BaseModel


class DeviceCodeRequest(BaseModel):
    """Request model for device code generation"""

    client_id: str
    scope: str | None = None


class DeviceCodeResponse(BaseModel):
    """Response model for device code generation"""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceTokenRequest(BaseModel):
    """Request model for device token polling"""

    grant_type: str
    device_code: str
    client_id: str


class DeviceTokenResponse(BaseModel):
    """Response model for device token"""

    access_token: str
    token_type: str
    expires_in: int
    scope: str
    refresh_token: str | None = None
