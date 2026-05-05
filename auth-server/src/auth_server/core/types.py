from typing import Literal, TypedDict

AllowedProvider = Literal["keycloak", "cognito", "entra"]


class AuthProviderConfig(TypedDict):
    display_name: str
    client_id: str
    client_secret: str
    auth_url: str
    token_url: str
    user_info_url: str
    logout_url: str
    scopes: list[str]
    response_type: str
    grant_type: str
    # Claims mapping for user info
    username_claim: str
    groups_claim: str
    email_claim: str
    name_claim: str
    enabled: bool


class EntraConfig(AuthProviderConfig):
    tenant_id: str
    jwks_url: str
    graph_url: str
    m2m_scope: str


class SessionCookieConfig(TypedDict):
    max_age_seconds: int
    secure: bool  # Set to false for development
    httponly: bool
    samesite: str
    domain: str


class OAuth2Providers(TypedDict):
    keycloak: AuthProviderConfig
    cognito: AuthProviderConfig
    entra: EntraConfig


class OAuth2Config(TypedDict):
    providers: OAuth2Providers
