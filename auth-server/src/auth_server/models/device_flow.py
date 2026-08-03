"""
Pydantic models for OAuth 2.0 Device Flow.
"""

from pydantic import BaseModel

from registry_pkgs.core.downstream_oauth import DeviceCodeResponse as DeviceCodeResponse


class DeviceCodeRequest(BaseModel):
    """Request model for device code generation"""

    client_id: str
    scope: str | None = None


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
