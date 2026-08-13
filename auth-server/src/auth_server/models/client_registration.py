"""Pydantic models for OAuth dynamic client registration."""

from pydantic import BaseModel


class ClientRegistrationRequest(BaseModel):
    """RFC 7591 client metadata accepted by the registration endpoints."""

    client_name: str | None = None
    client_uri: str | None = None
    redirect_uris: list[str] | None = None
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    scope: str | None = None
    contacts: list[str] | None = None
    token_endpoint_auth_method: str = "none"


class ClientRegistrationResponse(BaseModel):
    """Client metadata returned after successful dynamic registration."""

    client_id: str
    client_secret: str | None
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str
    client_id_issued_at: int
    client_secret_expires_at: int = 0
    client_name: str | None = None
    client_uri: str | None = None
    redirect_uris: list[str] | None = None
    scope: str | None = None
