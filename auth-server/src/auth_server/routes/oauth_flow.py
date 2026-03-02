"""
Combined OAuth routes: device flow, dynamic client registration,
and Authorization Code (PKCE) login/callback endpoints.
"""

import base64
import json
import logging
import os
import secrets
import time
import urllib.parse
from typing import Any

import jwt
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from auth_utils.jwt_utils import build_jwt_payload, encode_jwt, get_token_kid
from registry_pkgs.core.scopes import map_groups_to_scopes

from ..core.config import settings
from ..models.device_flow import DeviceApprovalRequest, DeviceCodeResponse, DeviceTokenResponse
from ..providers.factory import get_auth_provider
from ..services.cognito_validator_service import SimplifiedCognitoValidator
from ..services.user_service import user_service
from ..utils.security_mask import (
    anonymize_ip,
    hash_username,
    mask_headers,
    mask_sensitive_id,
    parse_server_and_tool_from_url,
)

# Create global validator instance
validator = SimplifiedCognitoValidator()

logger = logging.getLogger(__name__)

router = APIRouter()


# JWT / signer configuration (use settings)
SECRET_KEY = settings.secret_key
JWT_ISSUER = settings.jwt_issuer
JWT_AUDIENCE = settings.jwt_audience
JWT_SELF_SIGNED_KID = settings.jwt_self_signed_kid

# Signer for temporary OAuth sessions
signer = URLSafeTimedSerializer(SECRET_KEY)


def oauth_error_response(error: str, error_description: str = None, status_code: int = 400) -> JSONResponse:
    content = {"error": error}
    if error_description:
        content["error_description"] = error_description
    return JSONResponse(status_code=status_code, content=content)


# Shared in-memory state (centralized)
from ..core.state import (
    authorization_codes_storage,
    device_codes_storage,
    refresh_tokens_storage,
    registered_clients,
    user_codes_storage,
)


class ClientRegistrationRequest(BaseModel):
    client_name: str | None = Field(None)
    client_uri: str | None = Field(None)
    redirect_uris: list[str] | None = Field(None)
    grant_types: list[str] | None = Field(
        default=["authorization_code", "urn:ietf:params:oauth:grant-type:device_code"]
    )
    response_types: list[str] | None = Field(default=["code"])
    scope: str | None = Field(None)
    contacts: list[str] | None = Field(None)
    token_endpoint_auth_method: str | None = Field(default="client_secret_post")


class ClientRegistrationResponse(BaseModel):
    client_id: str
    client_secret: str | None
    client_id_issued_at: int
    client_secret_expires_at: int = 0
    client_name: str | None = None
    client_uri: str | None = None
    redirect_uris: list[str] | None = None
    grant_types: list[str] = []
    response_types: list[str] = []
    scope: str | None = None
    token_endpoint_auth_method: str = "client_secret_post"


@router.post("/oauth2/register", response_model=ClientRegistrationResponse)
async def register_client(registration: ClientRegistrationRequest, request: Request) -> ClientRegistrationResponse:
    try:
        client_id = f"mcp-client-{secrets.token_urlsafe(16)}"
        client_secret = secrets.token_urlsafe(32)
        issued_at = int(time.time())

        client_metadata = {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": issued_at,
            "client_secret_expires_at": 0,
            "client_name": registration.client_name or "MCP Client",
            "client_uri": registration.client_uri,
            "redirect_uris": registration.redirect_uris or [],
            "grant_types": registration.grant_types
            or ["authorization_code", "urn:ietf:params:oauth:grant-type:device_code"],
            "response_types": registration.response_types or ["code"],
            "scope": registration.scope or "servers-read agents-read",
            "token_endpoint_auth_method": registration.token_endpoint_auth_method or "client_secret_post",
            "contacts": registration.contacts or [],
            "registered_at": issued_at,
            "ip_address": request.client.host if request.client else "unknown",
        }

        registered_clients[client_id] = client_metadata

        logger.info(f"Registered new OAuth client: client_id={client_id}, name={client_metadata['client_name']}")

        return ClientRegistrationResponse(
            client_id=client_id,
            client_secret=client_secret,
            client_id_issued_at=issued_at,
            client_secret_expires_at=0,
            client_name=client_metadata["client_name"],
            client_uri=client_metadata["client_uri"],
            redirect_uris=client_metadata["redirect_uris"],
            grant_types=client_metadata["grant_types"],
            response_types=client_metadata["response_types"],
            scope=client_metadata["scope"],
            token_endpoint_auth_method=client_metadata["token_endpoint_auth_method"],
        )
    except Exception as e:
        logger.error(f"Client registration failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Client registration failed")


def get_client(client_id: str) -> dict[str, Any] | None:
    return registered_clients.get(client_id)


def validate_client_credentials(client_id: str, client_secret: str) -> bool:
    client = registered_clients.get(client_id)
    if not client:
        return False
    return client.get("client_secret") == client_secret


def list_registered_clients() -> list[dict[str, Any]]:
    return [
        {
            "client_id": client_id,
            "client_name": metadata.get("client_name"),
            "grant_types": metadata.get("grant_types"),
            "registered_at": metadata.get("registered_at"),
            "ip_address": metadata.get("ip_address"),
        }
        for client_id, metadata in registered_clients.items()
    ]


# Device Flow helpers
def generate_user_code() -> str:
    import string

    chars = string.ascii_uppercase + string.digits
    chars = chars.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    code = "".join(secrets.choice(chars) for _ in range(8))
    return f"{code[:4]}-{code[4:]}"


def cleanup_expired_device_codes():
    current_time = int(time.time())
    expired_codes = [code for code, data in device_codes_storage.items() if current_time > data["expires_at"]]
    for code in expired_codes:
        user_code = device_codes_storage[code]["user_code"]
        del device_codes_storage[code]
        if user_code in user_codes_storage:
            del user_codes_storage[user_code]
    if expired_codes:
        logger.info(f"Cleaned up {len(expired_codes)} expired device codes")


def cleanup_expired_authorization_codes():
    current_time = int(time.time())
    expired_codes = [code for code, data in authorization_codes_storage.items() if current_time > data["expires_at"]]
    for code in expired_codes:
        del authorization_codes_storage[code]
    if expired_codes:
        logger.info(f"Cleaned up {len(expired_codes)} expired authorization codes")


@router.post("/oauth2/device/code", response_model=DeviceCodeResponse)
async def device_authorization(
    req: Request, client_id: str = Form(...), scope: str | None = Form(None), resource: str | None = Form(None)
):
    cleanup_expired_device_codes()
    device_code = secrets.token_urlsafe(32)
    user_code = generate_user_code()

    auth_server_url = settings.auth_server_external_url
    if not auth_server_url:
        host = req.headers.get("host", "localhost:8888")
        scheme = "https" if req.headers.get("x-forwarded-proto") == "https" or req.url.scheme == "https" else "http"
        auth_server_url = f"{scheme}://{host}"

    verification_uri = f"{auth_server_url}/oauth2/device/verify"
    verification_uri_complete = f"{verification_uri}?user_code={user_code}"

    current_time = int(time.time())
    expires_at = current_time + settings.device_code_expiry_seconds

    device_codes_storage[device_code] = {
        "user_code": user_code,
        "client_id": client_id,
        "scope": scope or "",
        "resource": resource,
        "status": "pending",
        "created_at": current_time,
        "expires_at": expires_at,
        "token": None,
    }

    user_codes_storage[user_code] = device_code

    logger.info(f"Generated device code for client_id: {client_id}, user_code: {user_code}, resource: {resource}")

    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=settings.device_code_expiry_seconds,
        interval=settings.device_code_poll_interval,
    )


@router.get("/oauth2/device/verify", response_class=HTMLResponse)
async def device_verification_page(user_code: str | None = None):
    settings.auth_server_external_url.rstrip("/")
    # simplified HTML omitted for brevity - keep original UX
    html_content = f"""
    <!DOCTYPE html>
    <html><body><h1>Device Verification</h1>
    <form id="verifyForm"><input id="user_code" value="{user_code or ""}"/></form>
    <script>/* simple form */</script></body></html>
    """
    return HTMLResponse(content=html_content)


@router.post("/oauth2/device/approve")
async def approve_device(request: DeviceApprovalRequest):
    cleanup_expired_device_codes()
    device_code = user_codes_storage.get(request.user_code)
    if not device_code:
        raise HTTPException(status_code=404, detail="Invalid or expired user code")
    device_data = device_codes_storage.get(device_code)
    if not device_data:
        raise HTTPException(status_code=404, detail="Device code not found")
    current_time = int(time.time())
    if current_time > device_data["expires_at"]:
        raise HTTPException(status_code=400, detail="Device code expired")
    if device_data["status"] == "approved":
        return {"status": "already_approved", "message": "Device already approved"}

    # Generate token
    audience = device_data.get("resource") or JWT_AUDIENCE
    token_payload = build_jwt_payload(
        subject="device_user",
        issuer=JWT_ISSUER,
        audience=audience,
        expires_in_seconds=3600,
        iat=current_time,
        extra_claims={
            "client_id": device_data["client_id"],
            "scope": device_data["scope"],
            "token_use": "access",
        },
    )
    access_token = encode_jwt(token_payload, SECRET_KEY, kid=JWT_SELF_SIGNED_KID)
    device_data["status"] = "approved"
    device_data["token"] = access_token
    device_data["approved_at"] = current_time
    logger.info(f"Device approved for user_code: {request.user_code}")
    return {"status": "approved", "message": "Device verified successfully"}


@router.post("/oauth2/token", response_model=DeviceTokenResponse)
async def device_token(
    grant_type: str = Form(...),
    device_code: str = Form(None),
    client_id: str = Form(...),
    code: str = Form(None),
    code_verifier: str = Form(None),
    refresh_token: str = Form(None),
    redirect_uri: str = Form(None),
):
    logger.info("TOKEN ENDPOINT CALLED")
    logger.info(f"grant_type: {grant_type}")
    # Authorization Code Flow
    if grant_type == "authorization_code":
        cleanup_expired_authorization_codes()
        if not code or not redirect_uri:
            return oauth_error_response("invalid_request", "code and redirect_uri are required")
        auth_code_data = authorization_codes_storage.get(code)
        if not auth_code_data:
            return oauth_error_response("invalid_grant", "authorization code not found or expired")
        if auth_code_data.get("used"):
            del authorization_codes_storage[code]
            return oauth_error_response("invalid_grant", "authorization code already used")
        auth_code_data["used"] = True
        if auth_code_data["client_id"] != client_id:
            return oauth_error_response("invalid_client", "client_id mismatch")
        if auth_code_data["redirect_uri"] != redirect_uri:
            return oauth_error_response("invalid_grant", "redirect_uri mismatch")
        current_time = int(time.time())
        if current_time > auth_code_data["expires_at"]:
            del authorization_codes_storage[code]
            return oauth_error_response("invalid_grant", "authorization code expired")
        code_challenge = auth_code_data.get("code_challenge")
        if code_challenge:
            if not code_verifier:
                return oauth_error_response("invalid_request", "code_verifier required for PKCE")

            method = auth_code_data.get("code_challenge_method", "S256")
            # Compute challenge from verifier and compare with stored challenge
            if method == "S256":
                computed_challenge = create_s256_code_challenge(code_verifier)
            else:
                computed_challenge = code_verifier

            if computed_challenge != code_challenge:
                return oauth_error_response("invalid_grant", "code_verifier validation failed")

        user_info = auth_code_data["user_info"]
        audience = auth_code_data.get("resource") or JWT_AUDIENCE
        user_groups = user_info.get("groups", [])
        user_scopes = map_groups_to_scopes(user_groups) if user_groups else user_info.get("scopes", [])

        # Resolve user_id from MongoDB
        user_id = await user_service.resolve_user_id(user_info)

        token_payload = build_jwt_payload(
            subject=user_info["username"],
            issuer=JWT_ISSUER,
            audience=audience,
            expires_in_seconds=3600,
            iat=current_time,
            extra_claims={
                "name": user_info.get("name"),
                "idp_id": user_info.get("idp_id"),
                "user_id": user_id,
                "client_id": client_id,
                "scope": " ".join(user_scopes) if isinstance(user_scopes, list) else user_scopes,
                "groups": user_info.get("groups", []),
                "token_use": "access",
            },
        )
        access_token = encode_jwt(token_payload, SECRET_KEY, kid=JWT_SELF_SIGNED_KID)

        rt = secrets.token_urlsafe(32)
        refresh_expires_at = current_time + 1209600
        refresh_tokens_storage[rt] = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": token_payload["scope"],
            "expires_at": refresh_expires_at,
        }

        del authorization_codes_storage[code]

        return DeviceTokenResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=3600,
            scope=token_payload["scope"],
            refresh_token=rt,
        )

    elif grant_type == "urn:ietf:params:oauth:grant-type:device_code":
        cleanup_expired_device_codes()
        if not device_code:
            return oauth_error_response("invalid_request", "device_code is required")
        device_data = device_codes_storage.get(device_code)
        if not device_data:
            return oauth_error_response("invalid_grant", "device_code not found")
        if device_data["client_id"] != client_id:
            return oauth_error_response("invalid_client", "client_id mismatch")
        current_time = int(time.time())
        if current_time > device_data["expires_at"]:
            return oauth_error_response("expired_token", "device_code has expired")
        if device_data["status"] == "pending":
            return oauth_error_response("authorization_pending", "user has not yet authorized this request")
        if device_data["status"] == "denied":
            return oauth_error_response("access_denied", "user denied authorization")
        if device_data["status"] == "approved" and device_data["token"]:
            return DeviceTokenResponse(
                access_token=device_data["token"], token_type="Bearer", expires_in=3600, scope=device_data["scope"]
            )
        return oauth_error_response("server_error", "unexpected server state", 500)

    else:
        if grant_type == "refresh_token":
            if not refresh_token:
                return oauth_error_response("invalid_request", "refresh_token is required")
            rt_data = refresh_tokens_storage.get(refresh_token)
            if not rt_data:
                return oauth_error_response("invalid_grant", "refresh token invalid or expired")
            if rt_data.get("client_id") != client_id:
                return oauth_error_response("invalid_client", "client_id mismatch")
            now = int(time.time())
            if now > rt_data.get("expires_at", 0):
                del refresh_tokens_storage[refresh_token]
                return oauth_error_response("invalid_grant", "refresh token expired")

            user_info = rt_data["user_info"]
            audience = rt_data.get("audience") or JWT_AUDIENCE

            # Resolve user_id from MongoDB
            user_id = await user_service.resolve_user_id(user_info)

            token_payload = build_jwt_payload(
                subject=user_info["username"],
                issuer=JWT_ISSUER,
                audience=audience,
                expires_in_seconds=3600,
                iat=now,
                extra_claims={
                    "user_id": user_id,
                    "client_id": client_id,
                    "scope": rt_data.get("scope", ""),
                    "groups": user_info.get("groups", []),
                    "token_use": "access",
                },
            )

            access_token = encode_jwt(token_payload, SECRET_KEY, kid=JWT_SELF_SIGNED_KID)
            return DeviceTokenResponse(
                access_token=access_token,
                token_type="Bearer",
                expires_in=3600,
                scope=rt_data.get("scope", ""),
                refresh_token=refresh_token,
            )

        return oauth_error_response("unsupported_grant_type", f"grant_type '{grant_type}' is not supported")


@router.get("/oauth2/providers")
async def get_oauth2_providers():
    try:
        auth_provider_env = os.getenv("AUTH_PROVIDER")
        enabled = []
        for provider_name, config in settings.oauth2_config.get("providers", {}).items():
            if config.get("enabled", False):
                if auth_provider_env and provider_name != auth_provider_env:
                    continue
                enabled.append(
                    {"name": provider_name, "display_name": config.get("display_name", provider_name.title())}
                )
        return {"providers": enabled}
    except Exception as e:
        logger.error(f"Error getting OAuth2 providers: {e}")
        return {"providers": [], "error": str(e)}


@router.get(f"/oauth2/login/{'{provider}'}")
async def oauth2_login(
    provider: str,
    request: Request,
    redirect_uri: str = None,
    client_id: str = None,
    code_challenge: str = None,
    code_challenge_method: str = None,
    response_type: str = None,
    resource: str = None,
    state: str = None,
):
    try:
        if provider not in settings.oauth2_config.get("providers", {}):
            raise HTTPException(status_code=404, detail=f"Provider {provider} not found")
        provider_config = settings.oauth2_config["providers"][provider]
        if not provider_config.get("enabled", False):
            raise HTTPException(status_code=400, detail=f"Provider {provider} is disabled")

        client_state = state
        internal_state_data = {"nonce": secrets.token_urlsafe(24), "resource": resource, "client_state": client_state}
        internal_state = (
            base64.urlsafe_b64encode(
                _json := jwt.utils.force_bytes(_json := __import__("json").dumps(internal_state_data))
            )
            .decode()
            .rstrip("=")
        )

        session_data = {
            "state": internal_state,
            "client_state": client_state,
            "provider": provider,
            "redirect_uri": redirect_uri or settings.oauth2_config.get("registry", {}).get("success_redirect", "/"),
        }
        if client_id and response_type == "code":
            session_data["client_id"] = client_id
            session_data["client_redirect_uri"] = redirect_uri
            session_data["code_challenge"] = code_challenge
            session_data["code_challenge_method"] = code_challenge_method or "S256"
            if resource:
                session_data["resource"] = resource

        temp_session = signer.dumps(session_data)

        auth_server_url = settings.auth_server_external_url or settings.auth_server_url
        auth_server_url = auth_server_url.rstrip("/")
        callback_uri = f"{auth_server_url}/oauth2/callback/{provider}"

        auth_params = {
            "client_id": provider_config["client_id"],
            "response_type": provider_config["response_type"],
            "scope": " ".join(provider_config["scopes"]),
            "state": internal_state,
            "redirect_uri": callback_uri,
        }
        auth_url = f"{provider_config['auth_url']}?{urllib.parse.urlencode(auth_params)}"

        response = RedirectResponse(url=auth_url, status_code=302)
        response.set_cookie(
            key="oauth2_temp_session",
            value=temp_session,
            max_age=settings.oauth_session_ttl_seconds,
            httponly=True,
            samesite="lax",
        )
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating OAuth2 login for {provider}: {e}")
        error_url = settings.oauth2_config.get("registry", {}).get("error_redirect", "/login")
        return RedirectResponse(url=f"{error_url}?error=oauth2_init_failed", status_code=302)


@router.get(f"/oauth2/callback/{'{provider}'}")
async def oauth2_callback(
    provider: str,
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    oauth2_temp_session: str = Cookie(None),
):
    try:
        if error:
            logger.warning(f"OAuth2 error from {provider}: {error}")
            error_url = settings.oauth2_config.get("registry", {}).get("error_redirect", "/login")
            return RedirectResponse(url=f"{error_url}?error=oauth2_error&details={error}", status_code=302)
        if not code or not state or not oauth2_temp_session:
            raise HTTPException(status_code=400, detail="Missing required OAuth2 parameters")

        # Try to decode state to extract resource
        resource = None
        try:
            pad = "=" * (-len(state) % 4)
            state_decoded = __import__("json").loads(base64.urlsafe_b64decode(state + pad).decode())
            resource = state_decoded.get("resource")
        except Exception as e:
            logger.debug(f"Failed to decode state parameter: {e}")

        # Validate temporary session
        try:
            temp_session_data = signer.loads(oauth2_temp_session, max_age=settings.oauth_session_ttl_seconds)
        except (SignatureExpired, BadSignature):
            www_authenticate_parts = [f'Bearer realm="{settings.jwt_issuer}"']
            if resource:
                try:
                    from urllib.parse import urlparse

                    parsed = urlparse(resource)
                    path_parts = parsed.path.strip("/").split("/")
                    if len(path_parts) >= 1:
                        base = f"{parsed.scheme}://{parsed.netloc}"
                        resource_metadata_url = f"{base}/.well-known/oauth-protected-resource/{'/'.join(path_parts)}"
                        www_authenticate_parts.append(f'resource_metadata="{resource_metadata_url}"')
                except Exception as e:
                    logger.debug(f"Failed to generate resource metadata URL: {e}")
            www_authenticate = ", ".join(www_authenticate_parts)
            raise HTTPException(
                status_code=401,
                detail="OAuth session expired - please re-authenticate",
                headers={"WWW-Authenticate": www_authenticate},
            )

        # Decode internal state from temp session to compare client_state
        internal_state = temp_session_data.get("state")

        if state != internal_state:
            raise HTTPException(status_code=400, detail="Invalid state parameter")

        if provider != temp_session_data.get("provider"):
            raise HTTPException(status_code=400, detail="Provider mismatch")

        provider_config = settings.oauth2_config["providers"][provider]

        auth_server_url = settings.auth_server_external_url or settings.auth_server_url
        auth_server_url = auth_server_url.rstrip("/")

        token_data = await exchange_code_for_token(provider, code, provider_config, auth_server_url)

        # Extract user information from tokens or userinfo
        mapped_user = None
        try:
            if provider in ["cognito", "keycloak"]:
                if "id_token" in token_data:
                    id_claims = jwt.decode(token_data["id_token"], options={"verify_signature": False})
                    mapped_user = {
                        "username": id_claims.get("preferred_username") or id_claims.get("sub"),
                        "email": id_claims.get("email"),
                        "name": id_claims.get("name") or id_claims.get("given_name"),
                        "idp_id": id_claims.get("sub"),
                        "groups": id_claims.get("groups", []),
                    }
                else:
                    # Try to decode access_token without verification to extract claims
                    try:
                        access_claims = jwt.decode(token_data.get("access_token"), options={"verify_signature": False})
                        mapped_user = {
                            "username": access_claims.get("username") or access_claims.get("sub"),
                            "email": access_claims.get("email"),
                            "name": access_claims.get("name"),
                            "idp_id": access_claims.get("sub"),
                            "groups": access_claims.get("groups", []),
                        }
                    except Exception:
                        raise ValueError("No ID token and access token claims unavailable")
            elif provider == "entra":
                auth_provider = get_auth_provider("entra")
                user_info = auth_provider.get_user_info(
                    access_token=token_data.get("access_token"), id_token=token_data.get("id_token")
                )
                mapped_user = {
                    "username": user_info.get("username"),
                    "email": user_info.get("email"),
                    "name": user_info.get("name"),
                    "idp_id": user_info.get("id"),
                    "groups": user_info.get("groups", []),
                }
            else:
                user_info = await get_user_info(token_data.get("access_token"), provider_config)
                mapped_user = map_user_info(user_info, provider_config)
        except Exception as e:
            logger.warning(f"Falling back to userInfo on token parsing error: {e}")
            user_info = await get_user_info(token_data.get("access_token"), provider_config)
            mapped_user = map_user_info(user_info, provider_config)

        # Resolve user_id from MongoDB and add to mapped_user
        user_id = await user_service.resolve_user_id(mapped_user)
        if user_id:
            mapped_user["user_id"] = user_id
            logger.debug(f"Added user_id {user_id} to mapped_user")

        mapped_user["provider"] = provider
        # Always use OAuth client flow (both external clients and registry)
        client_id = temp_session_data.get("client_id") or settings.registry_app_name
        code_challenge = temp_session_data.get("code_challenge")
        code_challenge_method = temp_session_data.get("code_challenge_method", "S256")
        client_redirect_uri = temp_session_data.get("client_redirect_uri") or f"{settings.registry_url}/redirect"

        # Generate authorization code for OAuth client flow
        cleanup_expired_authorization_codes()
        authorization_code = secrets.token_urlsafe(32)
        current_time = int(time.time())
        expires_at = current_time + 600

        authorization_codes_storage[authorization_code] = {
            "token_data": token_data,
            "user_info": mapped_user,
            "client_id": client_id,
            "expires_at": expires_at,
            "used": False,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "redirect_uri": client_redirect_uri,
            "resource": temp_session_data.get("resource"),
            "created_at": current_time,
        }

        redirect_params = {"code": authorization_code}
        if temp_session_data.get("client_state"):
            redirect_params["state"] = temp_session_data.get("client_state")

        redirect_url = f"{client_redirect_uri}?{urllib.parse.urlencode(redirect_params)}"
        logger.info(
            f"OAuth2 login successful for user: {mapped_user['username']} via {provider}. Redirecting to {client_id}..."
        )

        response = RedirectResponse(url=redirect_url, status_code=302)
        response.delete_cookie("oauth2_temp_session")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in OAuth2 callback for {provider}: {e}")
        error_url = settings.oauth2_config.get("registry", {}).get("error_redirect", "/login")
        return RedirectResponse(url=f"{error_url}?error=oauth2_callback_failed", status_code=302)


async def exchange_code_for_token(provider: str, code: str, provider_config: dict, auth_server_url: str = None) -> dict:
    if auth_server_url is None:
        auth_server_url = settings.auth_server_url
    redirect_uri = f"{auth_server_url}/oauth2/callback/{provider}"
    async with __import__("httpx").AsyncClient() as client:
        token_data = {
            "grant_type": provider_config["grant_type"],
            "client_id": provider_config["client_id"],
            "client_secret": provider_config["client_secret"],
            "code": code,
            "redirect_uri": redirect_uri,
        }
        headers = {"Accept": "application/json"}
        response = await client.post(provider_config["token_url"], data=token_data, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_user_info(access_token: str, provider_config: dict) -> dict:
    async with __import__("httpx").AsyncClient() as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await client.get(provider_config["user_info_url"], headers=headers)
        response.raise_for_status()
        return response.json()


def map_user_info(user_info: dict, provider_config: dict) -> dict:
    """Map user info from OAuth provider to standard format.

    Args:
        user_info: Raw user info from provider's userinfo endpoint
        provider_config: Provider configuration with claim mappings

    Returns:
        Standardized user info dict with username, email, name, user_id, and groups
    """
    mapped = {
        "username": user_info.get(provider_config["username_claim"]),
        "email": user_info.get(provider_config["email_claim"]),
        "name": user_info.get(provider_config["name_claim"]),
        "idp_id": user_info.get("sub") or user_info.get("id"),
        "groups": [],
    }
    groups_claim = provider_config.get("groups_claim")
    if groups_claim and groups_claim in user_info:
        groups = user_info[groups_claim]
        if isinstance(groups, list):
            mapped["groups"] = groups
        elif isinstance(groups, str):
            mapped["groups"] = [groups]
    else:
        for possible_group_claim in ["cognito:groups", "groups", "custom:groups"]:
            if possible_group_claim in user_info:
                groups = user_info[possible_group_claim]
                if isinstance(groups, list):
                    mapped["groups"] = groups
                elif isinstance(groups, str):
                    mapped["groups"] = [groups]
                break
    return mapped


@router.get("/oauth2/logout/{provider}")
async def oauth2_logout(provider: str, request: Request, redirect_uri: str = None):
    try:
        if provider not in settings.oauth2_config.get("providers", {}):
            raise HTTPException(status_code=404, detail=f"Provider {provider} not found")
        provider_config = settings.oauth2_config["providers"][provider]
        logout_url = provider_config.get("logout_url")
        if not logout_url:
            redirect_url = redirect_uri or settings.oauth2_config.get("registry", {}).get("success_redirect", "/login")
            return RedirectResponse(url=redirect_url, status_code=302)
        full_redirect_uri = redirect_uri or "/logout"
        if not full_redirect_uri.startswith("http"):
            registry_base = settings.registry_url or "http://localhost"
            full_redirect_uri = f"{registry_base.rstrip('/')}{full_redirect_uri}"
        logout_params = {"client_id": provider_config["client_id"], "logout_uri": full_redirect_uri}
        logout_redirect_url = f"{logout_url}?{urllib.parse.urlencode(logout_params)}"
        return RedirectResponse(url=logout_redirect_url, status_code=302)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating logout for {provider}: {e}")
        redirect_url = redirect_uri or settings.oauth2_config.get("registry", {}).get("success_redirect", "/login")
        return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/validate")
async def validate_request(request: Request):
    """
    Validate a request by extracting configuration from headers and validating the bearer token.

    Expected headers:
    - Authorization: Bearer <token>
    - X-User-Pool-Id: <user_pool_id>
    - X-Client-Id: <client_id>
    - X-Region: <region> (optional, defaults to us-east-1)
    - X-Original-URL: <original_url> (optional, for scope validation)

    Returns:
        HTTP 200 with user info headers if valid, HTTP 401/403 if invalid

    Raises:
        HTTPException: If the token is missing, invalid, or configuration is incomplete
    """
    try:
        # Extract headers
        # Check for X-Authorization first (custom header used by this gateway)
        # Only if X-Authorization is not present, check standard Authorization header
        authorization = request.headers.get("X-Authorization")
        if not authorization:
            authorization = request.headers.get("Authorization")
        cookie_header = request.headers.get("Cookie", "")
        user_pool_id = request.headers.get("X-User-Pool-Id")
        client_id = request.headers.get("X-Client-Id")
        region = request.headers.get("X-Region", "us-east-1")
        original_url = request.headers.get("X-Original-URL")
        body = request.headers.get("X-Body")

        # Extract server_name from original_url early for logging
        server_name_from_url = None
        if original_url:
            try:
                from urllib.parse import urlparse

                parsed_url = urlparse(original_url)
                path = parsed_url.path.strip("/")
                path_parts = path.split("/") if path else []
                server_name_from_url = path_parts[0] if path_parts else None
                logger.info(f"Extracted server_name '{server_name_from_url}' from original_url: {original_url}")
            except Exception as e:
                logger.warning(f"Failed to extract server_name from original_url {original_url}: {e}")

        # Read request body
        request_payload = None
        try:
            if body:
                payload_text = body  # .decode('utf-8')
                logger.info(f"Raw Request Payload ({len(payload_text)} chars): {payload_text[:1000]}...")
                request_payload = json.loads(payload_text)
                logger.info(f"JSON RPC Request Payload: {json.dumps(request_payload, indent=2)}")
            else:
                logger.info("No request body provided, skipping payload parsing")
        except UnicodeDecodeError as e:
            logger.warning(f"Could not decode body as UTF-8: {e}")
        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse JSON RPC payload: {e}")
        except Exception as e:
            logger.error(f"Error reading request payload: {type(e).__name__}: {e}")

        # Log request for debugging with anonymized IP
        client_ip = request.client.host if request.client else "unknown"
        logger.info(f"Validation request from {anonymize_ip(client_ip)}")
        logger.info(f"Request Method: {request.method}")

        # Log masked HTTP headers for GDPR/SOX compliance
        all_headers = dict(request.headers)
        masked_headers = mask_headers(all_headers)
        logger.debug(f"HTTP Headers (masked): {json.dumps(masked_headers, indent=2)}")

        # Log specific headers for debugging with masked sensitive data
        logger.info(
            f"Key Headers: Authorization={bool(authorization)}, Cookie={bool(cookie_header)}, "
            f"User-Pool-Id={mask_sensitive_id(user_pool_id) if user_pool_id else 'None'}, "
            f"Client-Id={mask_sensitive_id(client_id) if client_id else 'None'}, "
            f"Region={region}, Original-URL={original_url}"
        )
        logger.info(f"Server Name from URL: {server_name_from_url}")

        # Initialize validation result
        validation_result = None

        # FIRST: Check for session cookie if present
        if "jarvis_registry_session=" in cookie_header:
            logger.info("Session cookie detected, attempting session validation")
            # Extract cookie value
            cookie_value = None
            for cookie in cookie_header.split(";"):
                if cookie.strip().startswith("jarvis_registry_session="):
                    cookie_value = cookie.strip().split("=", 1)[1]
                    break

            if cookie_value:
                try:
                    validation_result = validate_session_cookie(cookie_value)
                    # Log validation result without exposing username
                    safe_result = {k: v for k, v in validation_result.items() if k != "username"}
                    safe_result["username"] = hash_username(validation_result.get("username", ""))
                    logger.info(f"Session cookie validation result: {safe_result}")
                    logger.info(
                        f"Session cookie validation successful for user: {hash_username(validation_result['username'])}"
                    )
                except ValueError as e:
                    logger.warning(f"Session cookie validation failed: {e}")
                    # Fall through to JWT validation

        # SECOND: If no valid session cookie, check for JWT token
        if not validation_result:
            # Validate required headers for JWT
            if not authorization or not authorization.startswith("Bearer "):
                logger.warning("Missing or invalid Authorization header and no valid session cookie")
                raise HTTPException(
                    status_code=401,
                    detail="Missing or invalid Authorization header. Expected: Bearer <token> or valid session cookie",
                    headers={"WWW-Authenticate": "Bearer", "Connection": "close"},
                )

            # Extract token
            access_token = authorization.split(" ")[1]

            # FIRST: Check if this is a self-signed token (fast path detection by kid header OR issuer)
            # This must happen BEFORE provider-specific validation to avoid sending HS256 tokens to RS256 providers
            validation_result = None
            try:
                # Try to get the kid from header
                header_kid = get_token_kid(access_token)

                # If kid is our self-signed token identifier, validate as self-signed immediately
                if header_kid == JWT_SELF_SIGNED_KID:
                    logger.info("Detected self-signed token by kid header, validating...")
                    validation_result = validator.validate_self_signed_token(access_token)
                    logger.info(
                        f"Self-signed token validation successful for user: {hash_username(validation_result.get('username', ''))}"
                    )
            except Exception as e:
                logger.debug(f"Could not check JWT header kid: {e}")

            # If kid check didn't work, try checking issuer in payload
            if not validation_result:
                try:
                    unverified_claims = jwt.decode(access_token, options={"verify_signature": False})
                    if unverified_claims.get("iss") == JWT_ISSUER:
                        logger.info("Detected self-signed token by issuer, validating...")
                        validation_result = validator.validate_self_signed_token(access_token)
                        logger.info(
                            f"Self-signed token validation successful for user: {hash_username(validation_result.get('username', ''))}"
                        )
                except Exception as e:
                    logger.debug(f"Could not check JWT issuer for self-signed detection: {e}")

            # If not a self-signed token, use provider-specific validation
            if not validation_result:
                # Get authentication provider based on AUTH_PROVIDER environment variable
                try:
                    auth_provider = get_auth_provider()
                    logger.info(f"Using authentication provider: {auth_provider.__class__.__name__}")

                    # Provider-specific validation
                    if hasattr(auth_provider, "validate_token"):
                        # For Keycloak, Entra ID, etc. - no additional headers needed
                        validation_result = auth_provider.validate_token(access_token)
                        logger.info(f"Token validation successful using {auth_provider.__class__.__name__}")
                    else:
                        # Fallback to old validation for compatibility
                        if not user_pool_id:
                            logger.warning("Missing X-User-Pool-Id header for Cognito validation")
                            raise HTTPException(
                                status_code=400, detail="Missing X-User-Pool-Id header", headers={"Connection": "close"}
                            )

                        if not client_id:
                            logger.warning("Missing X-Client-Id header for Cognito validation")
                            raise HTTPException(
                                status_code=400, detail="Missing X-Client-Id header", headers={"Connection": "close"}
                            )

                        # Use old validator for backward compatibility
                        validation_result = validator.validate_token(
                            access_token=access_token, user_pool_id=user_pool_id, client_id=client_id, region=region
                        )

                except Exception as e:
                    logger.error(f"Authentication provider error: {e}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Authentication provider configuration error: {str(e)}",
                        headers={"Connection": "close"},
                    )

        logger.info(f"Token validation successful using method: {validation_result['method']}")

        # Parse server and tool information from original URL if available
        server_name = server_name_from_url  # Use the server_name we extracted earlier
        tool_name = None

        if original_url and request_payload:
            # We already extracted server_name above, now just get tool_name from URL parsing
            _, tool_name = parse_server_and_tool_from_url(original_url)
            logger.debug(f"Parsed from original URL: server='{server_name}', tool='{tool_name}'")

            # Try to extract tool name from request payload if not found in URL
            if server_name and not tool_name and request_payload:
                try:
                    # Look for tool name in JSON-RPC 2.0 format and other MCP patterns
                    if isinstance(request_payload, dict):
                        # JSON-RPC 2.0 format: method field contains the tool name
                        tool_name = request_payload.get("method")

                        # If not found in method, check other common patterns
                        if not tool_name:
                            tool_name = request_payload.get("tool") or request_payload.get("name")

                        # Check for nested tool reference in params
                        if not tool_name and "params" in request_payload:
                            params = request_payload["params"]
                            if isinstance(params, dict):
                                tool_name = params.get("name") or params.get("tool") or params.get("method")

                        logger.info(f"Extracted tool name from JSON-RPC payload: '{tool_name}'")
                    else:
                        logger.warning(f"Payload is not a dictionary: {type(request_payload)}")
                except Exception as e:
                    logger.error(f"Error processing request payload for tool extraction: {e}")

        # Validate scope-based access if we have server/tool information
        # For providers that use groups (Keycloak, Entra ID, Cognito), map groups to scopes
        user_groups = validation_result.get("groups", [])
        auth_method = validation_result.get("method", "")
        if user_groups and auth_method in ["keycloak", "entra", "cognito"]:
            # Map IdP groups to scopes using the group mappings
            user_scopes = map_groups_to_scopes(user_groups)
            logger.info(f"Mapped {auth_method} groups {user_groups} to scopes: {user_scopes}")
        else:
            user_scopes = validation_result.get("scopes", [])
        if server_name:
            # For ANY server access, enforce scope validation (fail closed principle)
            # This includes MCP initialization methods that may not have a specific tool

            method = tool_name if tool_name else "initialize"  # Default to initialize if no tool specified
            actual_tool_name = None

            # For tools/call, extract the actual tool name from params
            if method == "tools/call" and isinstance(request_payload, dict):
                params = request_payload.get("params", {})
                if isinstance(params, dict):
                    actual_tool_name = params.get("name")
                    logger.info(f"Extracted actual tool name for tools/call: '{actual_tool_name}'")

            # Check if user has any scopes - if not, deny access (fail closed)
            if not user_scopes:
                logger.warning(
                    f"Access denied for user {hash_username(validation_result.get('username', ''))} to {server_name}.{method} (tool: {actual_tool_name}) - no scopes configured"
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied to {server_name}.{method} - user has no scopes configured",
                    headers={"Connection": "close"},
                )

            if not validate_server_tool_access(server_name, method, actual_tool_name, user_scopes):
                logger.warning(
                    f"Access denied for user {hash_username(validation_result.get('username', ''))} to {server_name}.{method} (tool: {actual_tool_name})"
                )
                raise HTTPException(
                    status_code=403, detail=f"Access denied to {server_name}.{method}", headers={"Connection": "close"}
                )
            logger.info(f"Scope validation passed for {server_name}.{method} (tool: {actual_tool_name})")
        else:
            logger.debug("No server information available, skipping scope validation")

        # Prepare JSON response data
        response_data = {
            "valid": True,
            "username": validation_result.get("username") or "",
            "client_id": validation_result.get("client_id") or "",
            "scopes": user_scopes,
            "method": validation_result.get("method") or "",
            "groups": validation_result.get("groups", []),
            "server_name": server_name,
            "tool_name": tool_name,
        }
        logger.info(f"Full validation result: {json.dumps(validation_result, indent=2)}")
        logger.info(f"Response data being sent: {json.dumps(response_data, indent=2)}")
        # Create JSON response with headers that nginx can use
        response = JSONResponse(content=response_data, status_code=200)

        # Set headers for nginx auth_request_set directives
        response.headers["X-User"] = validation_result.get("username") or ""
        response.headers["X-Username"] = validation_result.get("username") or ""
        response.headers["X-Client-Id"] = validation_result.get("client_id") or ""
        response.headers["X-Scopes"] = " ".join(user_scopes)
        response.headers["X-Auth-Method"] = validation_result.get("method") or ""
        response.headers["X-Server-Name"] = server_name or ""
        response.headers["X-Tool-Name"] = tool_name or ""

        return response

    except ValueError as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=401, detail=str(e), headers={"WWW-Authenticate": "Bearer", "Connection": "close"}
        )
    except HTTPException as e:
        # If it's a 403 HTTPException, re-raise it as is
        if e.status_code == 403:
            raise
        # For other HTTPExceptions, let them fall through to general handler
        logger.error(f"HTTP error during validation: {e}")
        raise HTTPException(
            status_code=500, detail=f"Internal validation error: {str(e)}", headers={"Connection": "close"}
        )
    except Exception as e:
        logger.error(f"Unexpected error during validation: {e}")
        raise HTTPException(
            status_code=500, detail=f"Internal validation error: {str(e)}", headers={"Connection": "close"}
        )
    finally:
        pass


def validate_server_tool_access(server_name: str, method: str, tool_name: str, user_scopes: list[str]) -> bool:
    """
    Validate if the user has access to the specified server method/tool based on scopes.

    Args:
        server_name: Name of the MCP server
        method: Name of the method being accessed (e.g., 'initialize', 'notifications/initialized', 'tools/list')
        tool_name: Name of the specific tool being accessed (optional, for tools/call)
        user_scopes: List of user scopes from token

    Returns:
        True if access is allowed, False otherwise
    """
    try:
        # Verbose logging: Print input parameters
        logger.info("=== VALIDATE_SERVER_TOOL_ACCESS START ===")
        logger.info(f"Requested server: '{server_name}'")
        logger.info(f"Requested method: '{method}'")
        logger.info(f"Requested tool: '{tool_name}'")
        logger.info(f"User scopes: {user_scopes}")
        logger.info(
            f"Available scopes config keys: {list(settings.scopes_config.keys()) if settings.scopes_config else 'None'}"
        )

        if not settings.scopes_config:
            logger.warning("No scopes configuration loaded, allowing access")
            logger.info("=== VALIDATE_SERVER_TOOL_ACCESS END: ALLOWED (no config) ===")
            return True

        # Check each user scope to see if it grants access
        for scope in user_scopes:
            logger.info(f"--- Checking scope: '{scope}' ---")
            scope_config = settings.scopes_config.get(scope, [])

            if not scope_config:
                logger.info(f"Scope '{scope}' not found in configuration")
                continue

            logger.info(f"Scope '{scope}' config: {scope_config}")

            # The scope_config is directly a list of server configurations
            # since the permission type is already encoded in the scope name
            for server_config in scope_config:
                logger.info(f"  Examining server config: {server_config}")
                server_config_name = server_config.get("server")
                logger.info(f"  Server name in config: '{server_config_name}' vs requested: '{server_name}'")

                if _server_names_match(server_config_name, server_name):
                    logger.info("  ✓ Server name matches!")

                    # Check methods first
                    allowed_methods = server_config.get("methods", [])
                    logger.info(f"  Allowed methods for server '{server_name}': {allowed_methods}")
                    logger.info(f"  Checking if method '{method}' is in allowed methods...")

                    # Check if all methods are allowed (wildcard support)
                    has_wildcard_methods = "all" in allowed_methods or "*" in allowed_methods

                    # for all methods except tools/call we are good if the method is allowed
                    # for tools/call we need to do an extra validation to check if the tool
                    # itself is allowed or not
                    if (method in allowed_methods or has_wildcard_methods) and method != "tools/call":
                        logger.info(f"  ✓ Method '{method}' found in allowed methods!")
                        logger.info(f"Access granted: scope '{scope}' allows access to {server_name}.{method}")
                        logger.info("=== VALIDATE_SERVER_TOOL_ACCESS END: GRANTED ===")
                        return True

                    # Check tools if method not found in methods
                    allowed_tools = server_config.get("tools", [])
                    logger.info(f"  Allowed tools for server '{server_name}': {allowed_tools}")

                    # Check if all tools are allowed (wildcard support)
                    has_wildcard_tools = "all" in allowed_tools or "*" in allowed_tools

                    # For tools/call, check if the specific tool is allowed
                    if method == "tools/call" and tool_name:
                        logger.info(f"  Checking if tool '{tool_name}' is in allowed tools for tools/call...")
                        if tool_name in allowed_tools or has_wildcard_tools:
                            logger.info(f"  ✓ Tool '{tool_name}' found in allowed tools!")
                            logger.info(
                                f"Access granted: scope '{scope}' allows access to {server_name}.{method} for tool {tool_name}"
                            )
                            logger.info("=== VALIDATE_SERVER_TOOL_ACCESS END: GRANTED ===")
                            return True
                        else:
                            logger.info(f"  ✗ Tool '{tool_name}' NOT found in allowed tools")
                    else:
                        # For other methods, check if method is in tools list (backward compatibility)
                        logger.info(f"  Checking if method '{method}' is in allowed tools...")
                        if method in allowed_tools or has_wildcard_tools:
                            logger.info(f"  ✓ Method '{method}' found in allowed tools!")
                            logger.info(f"Access granted: scope '{scope}' allows access to {server_name}.{method}")
                            logger.info("=== VALIDATE_SERVER_TOOL_ACCESS END: GRANTED ===")
                            return True
                        else:
                            logger.info(f"  ✗ Method '{method}' NOT found in allowed tools")
                else:
                    logger.info("  ✗ Server name does not match")

        logger.warning(
            f"Access denied: no scope allows access to {server_name}.{method} (tool: {tool_name}) for user scopes: {user_scopes}"
        )
        logger.info("=== VALIDATE_SERVER_TOOL_ACCESS END: DENIED ===")
        return False

    except Exception as e:
        logger.error(f"Error validating server/tool access: {e}")
        logger.info("=== VALIDATE_SERVER_TOOL_ACCESS END: ERROR ===")
        return False  # Deny access on error


def _server_names_match(name1: str, name2: str) -> bool:
    """
    Compare two server names, normalizing for trailing slashes.
    Supports wildcard matching with '*'.

    Args:
        name1: First server name (can be '*' for wildcard)
        name2: Second server name

    Returns:
        True if names match (ignoring trailing slashes) or if name1 is '*', False otherwise
    """
    normalized_name1 = _normalize_server_name(name1)
    if normalized_name1 == "*":
        return True
    return normalized_name1 == _normalize_server_name(name2)


def _normalize_server_name(name: str) -> str:
    """
    Normalize server name by removing trailing slash for comparison.

    This handles cases where a server is registered with a trailing slash
    but accessed without one (or vice versa).

    Args:
        name: Server name to normalize

    Returns:
        Normalized server name (without trailing slash)
    """
    return name.rstrip("/") if name else name


def validate_session_cookie(cookie_value: str) -> dict[str, any]:
    """
    Validate session cookie using itsdangerous serializer.

    Args:
        cookie_value: The session cookie value

    Returns:
        Dict containing validation results matching JWT validation format
    Raises:
        ValueError: If cookie is invalid or expired
    """
    # Use global signer initialized at startup
    global signer
    if not signer:
        logger.warning("Global signer not configured for session cookie validation")
        raise ValueError("Session cookie validation not configured")

    try:
        # Decrypt cookie (max_age=28800 for 8 hours)
        data = signer.loads(cookie_value, max_age=28800)

        # Extract user info
        username = data.get("username")
        groups = data.get("groups", [])

        # Map groups to scopes using global settings.scopes_config
        scopes = map_groups_to_scopes(groups)

        logger.info(f"Session cookie validated for user: {hash_username(username)}")

        return {
            "valid": True,
            "username": username,
            "scopes": scopes,
            "method": "session_cookie",
            "groups": groups,
            "client_id": "",  # Not applicable for session
            "data": data,  # Include full data for consistency
        }
    except SignatureExpired:
        logger.warning("Session cookie has expired")
        raise ValueError("Session cookie has expired")
    except BadSignature:
        logger.warning("Invalid session cookie signature")
        raise ValueError("Invalid session cookie")
    except Exception as e:
        logger.error(f"Session cookie validation error: {e}")
        raise ValueError(f"Session cookie validation failed: {e}")
