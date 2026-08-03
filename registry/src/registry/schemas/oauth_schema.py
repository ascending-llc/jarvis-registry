import time
from dataclasses import dataclass, field
from typing import Any, TypedDict

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .enums import OAuthFlowStatus


class MCPClientContext(TypedDict):
    """OAuth 2.0 / PKCE parameters of an MCP client that initiated a downstream OAuth flow via
    the per-server `/downstream/oauth/authorize` endpoint (Layer B: registry-as-AS).

    Stored on the flow metadata so the callback can redirect the browser back to the MCP client
    (`redirect_uri`) and the `/token` endpoint can complete PKCE. Distinct from the Layer A PKCE
    the registry runs against the upstream provider.

    Defined here (not in ``auth/oauth/types.py``) because ``schemas`` must not import the
    ``auth.oauth`` package — doing so creates a circular import via the package ``__init__``.
    """

    redirect_uri: str
    client_id: str
    code_challenge: str
    state: str
    server_path: str


class OAuthTokens(BaseModel):
    """OAuth tokens"""

    access_token: str | None = Field(None, description="Access token (can be None if expired/deleted)")
    token_type: str = Field("Bearer", description="Token type")
    expires_in: int | None = Field(None, description="Expiration time (seconds)")
    refresh_token: str | None = Field(None, description="Refresh token")
    scope: str | None = Field(None, description="Authorization scope")
    obtained_at: int | None = Field(None, description="Obtained timestamp")
    expires_at: int | None = Field(None, description="Expiration timestamp")

    @classmethod
    @field_validator("expires_at", mode="before")
    def set_expires_at(cls, v: int | None, info: ValidationInfo) -> int | None:
        """Calculate expires_at based on expires_in if not provided"""
        if v is None and info.data.get("expires_in") is not None:
            return int(time.time()) + info.data["expires_in"]
        return v

    def model_post_init(self, __context: Any) -> None:
        """Validate that at least one token (access or refresh) is present"""
        if not self.access_token and not self.refresh_token:
            raise ValueError("At least one of access_token or refresh_token must be provided")


class OAuthClientInformation(BaseModel):
    """OAuth client information"""

    client_id: str = Field(..., description="Client ID")
    client_secret: str | None = Field(None, description="Client secret")
    redirect_uris: list[str] | None = Field(None, description="Redirect URI list")
    scope: str | None = Field(None, description="Authorization scope")
    grant_types: list[str] | None = Field(None, description="Grant type list")
    additional_params: dict[str, Any] | None = Field(None, description="Additional OAuth parameters")


class OAuthMetadata(BaseModel):
    """OAuth metadata"""

    issuer: str | None = Field(None, description="Issuer")
    authorization_endpoint: str = Field(..., description="Authorization endpoint")
    token_endpoint: str = Field(..., description="Token endpoint")
    registration_endpoint: str | None = Field(None, description="Registration endpoint")
    scopes_supported: list[str] | None = Field(None, description="Supported scopes")
    response_types_supported: list[str] | None = Field(None, description="Supported response types")
    grant_types_supported: list[str] | None = Field(None, description="Supported grant types")
    token_endpoint_auth_methods_supported: list[str] | None = Field(
        None, description="Supported token endpoint authentication methods"
    )
    code_challenge_methods_supported: list[str] | None = Field(None, description="Supported code challenge methods")


class OAuthProtectedResourceMetadata(BaseModel):
    """OAuth protected resource metadata"""

    resource: str | None = Field(None, description="Resource identifier")
    authorization_servers: list[str] | None = Field(None, description="Authorization server list")
    scopes_supported: list[str] | None = Field(None, description="Supported scopes")


class MCPOAuthFlowMetadata(BaseModel):
    """MCP OAuth flow metadata"""

    server_name: str = Field(..., description="Server name")
    server_path: str = Field(..., description="Server path")
    server_id: str = Field(..., description="Server id")
    user_id: str = Field(..., description="User ID")
    authorization_url: str = Field(..., description="Authorization URL")
    state: str = Field(..., description="State parameter")
    code_verifier: str = Field(..., description="PKCE code_verifier")
    client_info: OAuthClientInformation = Field(..., description="Client information")
    metadata: OAuthMetadata = Field(..., description="OAuth metadata")
    resource_metadata: OAuthProtectedResourceMetadata | None = Field(None, description="Resource metadata")
    mcp_client_context: MCPClientContext | None = Field(
        None,
        description="OAuth/PKCE context of an MCP client that initiated a downstream OAuth flow (Layer B)",
    )
    device_code: str | None = Field(
        None,
        description="Device code when a browserless downstream client initiated the Layer B flow",
    )


@dataclass
class OAuthFlow:
    """OAuth flow"""

    flow_id: str
    server_id: str
    server_name: str
    user_id: str
    code_verifier: str
    state: str
    status: OAuthFlowStatus = OAuthFlowStatus.PENDING
    created_at: float = field(default_factory=time.time)  # Use dataclasses.field instead of Pydantic Field
    completed_at: float | None = None
    tokens: OAuthTokens | None = None
    error: str | None = None
    metadata: MCPOAuthFlowMetadata | None = None
