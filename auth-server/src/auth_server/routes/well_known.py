"""
OAuth 2.0 .well-known endpoints for auth server.

Implements RFC 8414 (OAuth 2.0 Authorization Server Metadata) and OIDC Discovery specifications.
Also implements RFC 9728 Protected Resource Metadata.
"""

import logging

from fastapi import APIRouter, HTTPException

from registry_pkgs.core.jwt_utils import build_jwks

# Import settings
from ..core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_metadata():
    """
    OAuth 2.0 Authorization Server Metadata (RFC 8414).

    Provides metadata about the OAuth 2.0 authorization server to enable
    automatic client configuration and discovery.

    Per RFC 8414, the issuer MUST be at the root origin without any prefix.
    Operational endpoints use auth_server_url which includes the prefix.
    """

    auth_server_url = settings.auth_server_external_url

    # Get current auth provider from settings
    auth_provider = settings.auth_provider

    # Load scopes from scopes.yml
    scopes_config = settings.scopes_config

    # Get all scope names (excluding group_mappings)
    scope_names = [key for key in scopes_config if key != "group_mappings"]

    return {
        "issuer": settings.jwt_issuer,
        "authorization_endpoint": f"{auth_server_url}/oauth2/login/{auth_provider}",
        "token_endpoint": f"{auth_server_url}/oauth2/token",
        "device_authorization_endpoint": f"{auth_server_url}/oauth2/device/code",
        "registration_endpoint": f"{auth_server_url}/oauth2/register",
        "jwks_uri": f"{settings.jwt_issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "urn:ietf:params:oauth:grant-type:device_code",
        ],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": scope_names,
        "service_documentation": f"{auth_server_url}/docs",
    }


@router.get("/.well-known/openid-configuration")
async def openid_configuration():
    """
    OpenID Connect Discovery endpoint.

    Provides OpenID Connect configuration metadata for clients that
    expect OIDC discovery.

    Per OIDC spec, the issuer MUST be at the root origin without any prefix.
    Operational endpoints include the prefix if configured.
    use auth_server_url which includes the prefix.
    """
    auth_server_url = settings.auth_server_external_url

    # Get current auth provider from settings
    auth_provider = settings.auth_provider

    return {
        "issuer": settings.jwt_issuer,
        "authorization_endpoint": f"{auth_server_url}/oauth2/login/{auth_provider}",
        "token_endpoint": f"{auth_server_url}/oauth2/token",
        "device_authorization_endpoint": f"{auth_server_url}/oauth2/device/code",
        "registration_endpoint": f"{auth_server_url}/oauth2/register",
        "userinfo_endpoint": f"{auth_server_url}/oauth2/userinfo",
        "jwks_uri": f"{settings.jwt_issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile", "email"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": ["sub", "email", "name", "groups"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "urn:ietf:params:oauth:grant-type:device_code",
        ],
    }


@router.get("/.well-known/jwks.json")
async def jwks_endpoint():
    """
    JSON Web Key Set (JWKS) endpoint (RFC 7517).

    Returns the RSA public key used to sign self-signed RS256 JWTs issued by this
    auth server. Token consumers (e.g. the registry service) can fetch this endpoint
    to obtain the public key and verify tokens without sharing a secret.

    The response contains a single JWK entry with:
    - ``kty``: "RSA"
    - ``use``: "sig"
    - ``alg``: "RS256"
    - ``kid``: matches the ``kid`` embedded in issued token headers
    - ``n`` / ``e``: base64url-encoded RSA modulus and public exponent

    Raises:
        HTTPException 500: If the public key is not configured or cannot be parsed.
    """
    if not settings.jwt_public_key:
        logger.error("JWT_PUBLIC_KEY is not configured")
        raise HTTPException(status_code=500, detail="Server configuration error: JWT_PUBLIC_KEY not configured")
    try:
        return build_jwks(settings.jwt_public_key, settings.jwt_self_signed_kid)
    except Exception as e:
        logger.error(f"Failed to build JWKS from public key: {e}")
        raise HTTPException(status_code=500, detail="Server configuration error: invalid JWT public key")


@router.get(f"/.well-known/oauth-protected-resource{settings.service_base_path}/proxy/{{server_path:path}}")
async def protected_resource_metadata(server_path: str):
    return {
        "resource": f"{settings.jwt_issuer}{settings.service_base_path}/proxy/{server_path}",
        "authorization_server": settings.jwt_issuer,
        "scopes_supported": " ".join(settings.scopes_set),
        "bearer_methods_supported": ["header"],
    }
