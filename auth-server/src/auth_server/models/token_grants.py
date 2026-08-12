"""Typed requests accepted by the shared OAuth token endpoint."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from registry_pkgs.core.client_categories import (
    AUTHORIZATION_CODE_GRANT_TYPE,
    REFRESH_TOKEN_GRANT_TYPE,
)
from registry_pkgs.core.downstream_oauth import DEVICE_CODE_GRANT_TYPE


class OAuthTokenRequest(BaseModel):
    """Common token request fields, including unsupported grants handled by the endpoint."""

    model_config = ConfigDict(extra="ignore", strict=True)

    grant_type: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: str | None = None


class AuthorizationCodeTokenRequest(OAuthTokenRequest):
    """Authorization-code exchange parameters."""

    grant_type: Literal[AUTHORIZATION_CODE_GRANT_TYPE]
    code: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)
    code_verifier: str | None = None


class DeviceCodeTokenRequest(OAuthTokenRequest):
    """Device-code polling parameters."""

    grant_type: Literal[DEVICE_CODE_GRANT_TYPE]
    device_code: str = Field(min_length=1)


class RefreshTokenRequest(OAuthTokenRequest):
    """Refresh-token exchange parameters."""

    grant_type: Literal[REFRESH_TOKEN_GRANT_TYPE]
    refresh_token: str = Field(min_length=1)


type TokenGrantRequest = (
    AuthorizationCodeTokenRequest | DeviceCodeTokenRequest | RefreshTokenRequest | OAuthTokenRequest
)
