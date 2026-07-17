"""
Combined OAuth routes: device flow, dynamic client registration,
and Authorization Code (PKCE) login/callback endpoints.
"""

import base64
import hmac
import json
import logging
import secrets
import time
from typing import Any, cast
from urllib.parse import urlencode, urlparse

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from registry_pkgs.core.consent_store import PENDING_CONSENT_TTL_SECONDS, ConsentStore, PendingConsentStore
from registry_pkgs.core.downstream_oauth import oauth_error_payload
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token
from registry_pkgs.core.jwt_utils import (
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    decode_jwt_unverified,
    decode_jwt_with_jwk,
    find_matching_jwk,
    get_token_kid,
)
from registry_pkgs.core.oauth_state_store import REFRESH_TOKEN_TTL_SECONDS, OAuthStateStoreProtocol
from registry_pkgs.core.redirect_uri import redirect_uri_matches, validate_registration_redirect_uri
from registry_pkgs.core.scopes import map_groups_to_scopes

from ..core.config import settings
from ..core.types import AllowedProvider, AuthProviderConfig, EntraConfig, OAuth2Config
from ..deps import (
    check_if_https,
    get_auth_provider,
    get_consent_store,
    get_oauth2_config,
    get_oauth_state_store,
    get_pending_consent_store,
    get_signer,
    get_user_service,
    get_validator,
)
from ..models.device_flow import DeviceCodeResponse, DeviceTokenResponse
from ..providers.base import AuthProvider
from ..services.cognito_validator_service import SimplifiedCognitoValidator
from ..services.user_service import UserService
from ..utils.security_mask import (
    anonymize_ip,
    hash_username,
    mask_headers,
    mask_sensitive_id,
    parse_server_and_tool_from_url,
)
from .consent_templates import (
    render_consent_error_page,
    render_consent_page,
    render_device_approved_page,
    render_device_code_confirm_page,
    render_device_code_entry_page,
    render_device_denied_page,
    render_device_link_error_page,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# JWT / signer configuration (use settings)
JWT_ISSUER = settings.jwt_issuer
JWT_SELF_SIGNED_KID = settings.jwt_self_signed_kid
# All access tokens issued by /oauth2/token (+ device flow) are managed-agent (proxy) tokens.
JWT_TOKEN_CONFIG = settings.jwt_token_config

_OIDC_TOKEN_ALGORITHMS = ["RS256"]


def oauth_error_response(error: str, error_description: str | None = None, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=oauth_error_payload(error, error_description))


def _provider_token_issuers(provider: AllowedProvider, auth_provider: AuthProvider) -> list[str]:
    if provider == "keycloak":
        issuer_candidates = [
            getattr(auth_provider, "external_realm_url", None),
            getattr(auth_provider, "realm_url", None),
            f"http://localhost:8080/realms/{getattr(auth_provider, 'realm', '')}",
        ]
        return [issuer for issuer in issuer_candidates if issuer]

    issuer = getattr(auth_provider, "issuer", None)
    if not issuer:
        raise InvalidTokenError(f"Provider {provider} does not expose an issuer for token verification")

    return [issuer]


def _provider_token_audience(provider: AllowedProvider, auth_provider: AuthProvider) -> str | list[str] | None:
    client_id = getattr(auth_provider, "client_id", None)
    if not client_id:
        raise InvalidTokenError(f"Provider {provider} does not expose a client_id for token verification")

    if provider == "keycloak":
        audiences = ["account", client_id, getattr(auth_provider, "m2m_client_id", client_id)]
        return list(dict.fromkeys(audiences))

    # Cognito access tokens omit the standard 'aud' claim; skipping audience
    # verification avoids InvalidAudienceError for both id_token and access_token flows.
    return None


async def _decode_oidc_provider_token(
    token: str,
    provider: AllowedProvider,
    auth_provider: AuthProvider,
) -> dict[str, Any]:
    jwks = await auth_provider.get_jwks()
    matching_key = find_matching_jwk(jwks, get_token_kid(token))
    audience = _provider_token_audience(provider, auth_provider)

    last_issuer_error: InvalidIssuerError | None = None
    for issuer in _provider_token_issuers(provider, auth_provider):
        try:
            return decode_jwt_with_jwk(
                token,
                matching_key,
                algorithms=_OIDC_TOKEN_ALGORITHMS,
                issuer=issuer,
                audience=audience,
            )
        except InvalidIssuerError as e:
            last_issuer_error = e

    raise last_issuer_error or ValueError(f"Token issuer is not trusted for provider {provider}")


class ClientRegistrationRequest(BaseModel):
    client_name: str | None = None
    client_uri: str | None = None
    redirect_uris: list[str] | None = None
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    scope: str | None = None
    contacts: list[str] | None = None
    token_endpoint_auth_method: str = "none"


class ClientRegistrationResponse(BaseModel):
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


DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS = frozenset({"none", "client_secret_post"})


def _is_registry_client(client_id: str) -> bool:
    return client_id == settings.registry_app_name


def _is_registered_redirect_uri(client_metadata: dict[str, Any], redirect_uri: str) -> bool:
    registered_redirect_uris = client_metadata.get("redirect_uris") or []
    return any(redirect_uri_matches(redirect_uri, registered) for registered in registered_redirect_uris)


def _get_unknown_client_response() -> JSONResponse:
    return oauth_error_response("invalid_client", "Unknown client_id")


def _validate_registration_redirect_uris(redirect_uris: list[str] | None) -> JSONResponse | None:
    """Reject a DCR request whose redirect_uris are missing or structurally unsafe.

    Returns an RFC 7591 §3.2.2 ``invalid_redirect_uri`` OAuth error response (not a ``{"detail": …}``
    body) so spec-compliant clients (Cline, VS Code) can parse the failure; ``None`` when valid.
    """
    uris = redirect_uris or []
    if not uris:
        return oauth_error_response("invalid_redirect_uri", "at least one redirect_uri is required")
    for uri in uris:
        try:
            validate_registration_redirect_uri(uri)
        except ValueError as e:
            return oauth_error_response("invalid_redirect_uri", str(e))
    return None


def _validate_known_client_for_redirect(
    client_id: str,
    redirect_uri: str,
    store: OAuthStateStoreProtocol,
) -> JSONResponse | None:
    if _is_registry_client(client_id):
        return None

    client_metadata = store.get_client(client_id)
    if client_metadata is None:
        return _get_unknown_client_response()

    if not _is_registered_redirect_uri(client_metadata, redirect_uri):
        return oauth_error_response("invalid_request", "redirect_uri is not registered for this client")

    return None


def _validate_known_client(
    client_id: str,
    store: OAuthStateStoreProtocol,
) -> JSONResponse | None:
    if _is_registry_client(client_id):
        return None

    if store.get_client(client_id) is None:
        return _get_unknown_client_response()

    return None


def _auth_server_route_path(path: str) -> str:
    prefix = settings.auth_server_api_prefix.rstrip("/") if settings.auth_server_api_prefix else ""
    return f"{prefix}{path}"


def _auth_server_external_url(path: str) -> str:
    base_url = settings.auth_server_external_url.rstrip("/")
    prefix = settings.auth_server_api_prefix.rstrip("/") if settings.auth_server_api_prefix else ""
    if prefix and base_url.endswith(prefix):
        return f"{base_url}{path}"
    return f"{base_url}{prefix}{path}"


def _normalize_user_code(user_code: str) -> str:
    compact = "".join(char for char in user_code.upper() if char.isalnum())
    if len(compact) == 8:
        return f"{compact[:4]}-{compact[4:]}"
    return user_code.strip().upper()


def _redirect_to_provider(
    provider: AllowedProvider,
    provider_config: AuthProviderConfig | EntraConfig,
    session_data: dict[str, Any],
    is_https: bool,
    signer: URLSafeTimedSerializer,
) -> RedirectResponse:
    """Build the signed temp session cookie and 302 to the configured IdP."""
    temp_session = signer.dumps(session_data)
    callback_uri = _auth_server_external_url(f"/oauth2/callback/{provider}")

    auth_params = {
        "client_id": provider_config["client_id"],
        "response_type": provider_config["response_type"],
        "scope": " ".join(provider_config["scopes"]),
        "state": session_data["state"],
        "redirect_uri": callback_uri,
    }
    auth_url = f"{provider_config['auth_url']}?{urlencode(auth_params)}"

    response = RedirectResponse(url=auth_url, status_code=302)
    response.set_cookie(
        key=settings.oauth2_temp_session_cookie_name,
        value=temp_session,
        max_age=settings.oauth_session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure and is_https,
        samesite="lax",
    )
    return response


def _redirect_to_pending_consent(
    pending_payload: dict[str, Any],
    ttl_seconds: int,
    is_https: bool,
    pending_store: PendingConsentStore,
) -> RedirectResponse:
    """Save a pending-consent nonce and 302 to /oauth2/consent; shared tail of the device-flow and
    Authorization-Code-Grant consent detours in oauth2_callback — the only difference between
    callers is what they put in pending_payload and how long the detour should live for.
    """
    nonce = secrets.token_urlsafe(32)
    pending_store.save(nonce, pending_payload, ttl_seconds=ttl_seconds)

    consent_url = f"{_auth_server_external_url('/oauth2/consent')}?nonce={nonce}"
    response = RedirectResponse(url=consent_url, status_code=302)
    response.set_cookie(
        key=settings.oauth2_consent_nonce_cookie_name,
        value=nonce,
        max_age=ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure and is_https,
        samesite="lax",
    )
    response.delete_cookie(settings.oauth2_temp_session_cookie_name)
    return response


def _finish_oauth2_callback(
    token_data: dict[str, Any],
    mapped_user: dict[str, Any],
    session_data: dict[str, Any],
    resolved_scopes: list[str],
    store: OAuthStateStoreProtocol,
) -> RedirectResponse:
    """Mint our own authorization code and redirect to the MCP client's redirect_uri."""
    client_redirect_uri = session_data["client_redirect_uri"]
    authorization_code = secrets.token_urlsafe(32)
    current_time = int(time.time())
    expires_at = current_time + 600

    store.save_authcode(
        authorization_code,
        {
            "token_data": token_data,
            "user_info": mapped_user,
            "client_id": session_data["client_id"],
            "expires_at": expires_at,
            "code_challenge": session_data["code_challenge"],
            "code_challenge_method": session_data["code_challenge_method"],
            "redirect_uri": client_redirect_uri,
            "resource": session_data.get("resource"),
            "created_at": current_time,
            "resolved_scope": resolved_scopes,
        },
    )

    redirect_params = {"code": authorization_code}
    client_state = session_data.get("client_state")
    if client_state:
        redirect_params["state"] = client_state

    redirect_url = f"{client_redirect_uri}?{urlencode(redirect_params)}"
    logger.info("OAuth2 login successful, redirecting to %s...", redirect_url)

    response = RedirectResponse(url=redirect_url, status_code=302)
    response.delete_cookie(settings.oauth2_temp_session_cookie_name)
    return response


def _finish_device_callback(
    device_code: str,
    mapped_user: dict[str, Any],
    resolved_scopes: list[str],
    store: OAuthStateStoreProtocol,
) -> HTMLResponse:
    """Record a verified user's approval; token minting happens when the device polls /oauth2/token."""
    device_data = store.get_device_code(device_code)
    if device_data is None:
        return HTMLResponse(render_device_link_error_page(), status_code=400)

    updated = dict(device_data)
    updated["status"] = "approved"
    updated["mapped_user"] = mapped_user
    updated["resolved_scope"] = resolved_scopes
    store.update_device_code(device_code, updated)
    return HTMLResponse(render_device_approved_page())


def _finish_device_denial(device_code: str, store: OAuthStateStoreProtocol) -> HTMLResponse:
    device_data = store.get_device_code(device_code)
    if device_data is not None:
        updated = dict(device_data)
        updated["status"] = "denied"
        store.update_device_code(device_code, updated)
    return HTMLResponse(render_device_denied_page())


@router.post("/oauth2/register", response_model=ClientRegistrationResponse, response_model_exclude_none=True)
async def register_client(
    registration: ClientRegistrationRequest,
    request: Request,
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
) -> ClientRegistrationResponse | JSONResponse:
    try:
        logger.info(
            f"incoming DCR request. client_name: {registration.client_name}, grant_types: {registration.grant_types}, "
            f"response_types: {registration.response_types}, scope: {registration.scope}, "
            f"token_endpoint_auth_method: {registration.token_endpoint_auth_method}."
        )

        redirect_uri_error = _validate_registration_redirect_uris(registration.redirect_uris)
        if redirect_uri_error is not None:
            logger.warning(
                "DCR request rejected: invalid redirect_uri. client_name=%s, redirect_uris=%s",
                registration.client_name,
                registration.redirect_uris,
            )
            return redirect_uri_error

        client_id = f"mcp-client-{secrets.token_urlsafe(16)}"

        requested_auth_method = registration.token_endpoint_auth_method
        if requested_auth_method in SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS:
            token_endpoint_auth_method = requested_auth_method
        else:
            logger.warning(
                "DCR client requested unsupported token_endpoint_auth_method=%s; substituting 'none'. client_name=%s",
                requested_auth_method,
                registration.client_name,
            )
            token_endpoint_auth_method = "none"

        client_secret: str | None = None
        if token_endpoint_auth_method == "client_secret_post":
            client_secret = secrets.token_urlsafe(32)

        grant_types = ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE]
        response_types = ["code"]

        issued_at = int(time.time())

        client_metadata: dict[str, Any] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": issued_at,
            "client_secret_expires_at": 0,
            "client_name": registration.client_name or "MCP Client",
            "client_uri": registration.client_uri,
            "redirect_uris": registration.redirect_uris or [],
            "grant_types": grant_types,
            "response_types": response_types,
            "scope": registration.scope or "servers-read agents-read",
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "contacts": registration.contacts or [],
            "registered_at": issued_at,
            "ip_address": request.client.host if request.client else "unknown",
        }

        store.save_client(client_id, client_metadata)

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
    except HTTPException:
        raise
    except Exception:
        logger.exception("Client registration failed")

        raise HTTPException(status_code=500, detail="Client registration failed")


# Device Flow helpers
def generate_user_code() -> str:
    import string

    chars = string.ascii_uppercase + string.digits
    chars = chars.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    code = "".join(secrets.choice(chars) for _ in range(8))
    return f"{code[:4]}-{code[4:]}"


@router.post("/oauth2/device/code", response_model=DeviceCodeResponse, response_model_exclude_none=True)
async def device_authorization(
    req: Request,
    client_id: str = Form(...),
    scope: str | None = Form(None),
    resource: str | None = Form(None),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
):
    try:
        client_error = _validate_known_client(client_id, store)
        if client_error is not None:
            return client_error

        client_metadata = store.get_client(client_id) or {}
        if DEVICE_CODE_GRANT_TYPE not in (client_metadata.get("grant_types") or []):
            return oauth_error_response(
                "unauthorized_client",
                "client is not registered for the device_code grant type",
            )

        device_code = secrets.token_urlsafe(32)
        user_code = generate_user_code()

        verification_uri = _auth_server_external_url("/oauth2/device/verify")
        if not settings.auth_server_external_url:
            host = req.headers.get("host", "localhost:8888")
            scheme = "https" if req.headers.get("x-forwarded-proto") == "https" or req.url.scheme == "https" else "http"
            verification_uri = f"{scheme}://{host}{_auth_server_route_path('/oauth2/device/verify')}"
        verification_uri_complete = f"{verification_uri}?user_code={user_code}"

        current_time = int(time.time())
        expires_at = current_time + settings.device_code_expiry_seconds

        device_data = {
            "user_code": user_code,
            "client_id": client_id,
            "scope": scope or "",
            "resource": resource,
            "status": "pending",
            "created_at": current_time,
            "expires_at": expires_at,
            "mapped_user": None,
            "resolved_scope": None,
        }

        store.save_device_authorization(
            device_code=device_code,
            user_code=user_code,
            data=device_data,
            ttl_seconds=settings.device_code_expiry_seconds,
        )

        logger.info(f"Generated device code for client_id: {client_id}, user_code: {user_code}, resource: {resource}")

        return DeviceCodeResponse(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=verification_uri_complete,
            expires_in=settings.device_code_expiry_seconds,
            interval=settings.device_code_poll_interval,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Device authorization request failed for client_id=%s", client_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/oauth2/device/verify", response_class=HTMLResponse)
async def device_verify_entry(
    user_code: str | None = None,
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
) -> HTMLResponse:
    if not user_code:
        return HTMLResponse(
            render_device_code_entry_page(verify_action=_auth_server_route_path("/oauth2/device/verify"))
        )

    normalized = _normalize_user_code(user_code)
    device_code = store.get_user_code(normalized)
    device_data = store.get_device_code(device_code) if device_code else None
    if device_data is None or device_data["status"] != "pending":
        return HTMLResponse(render_device_link_error_page(), status_code=400)

    return HTMLResponse(
        render_device_code_confirm_page(
            user_code=device_data["user_code"],
            verify_action=_auth_server_route_path("/oauth2/device/verify"),
        )
    )


@router.post("/oauth2/device/verify", response_class=HTMLResponse)
async def device_verify_continue(
    user_code: str = Form(...),
    is_https: bool = Depends(check_if_https),
    signer: URLSafeTimedSerializer = Depends(get_signer),
    oauth2_config: OAuth2Config = Depends(get_oauth2_config),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
) -> Response:
    try:
        normalized = _normalize_user_code(user_code)
        device_code = store.get_user_code(normalized)
        device_data = store.get_device_code(device_code) if device_code else None
        if device_data is None or device_data["status"] != "pending":
            return HTMLResponse(render_device_link_error_page(), status_code=400)

        provider = settings.auth_provider
        provider_config = oauth2_config["providers"][provider]
        internal_state = (
            base64.urlsafe_b64encode(json.dumps({"nonce": secrets.token_urlsafe(24)}).encode("utf-8"))
            .decode()
            .rstrip("=")
        )

        session_data = {
            "flow_type": "device",
            "device_code": device_code,
            "provider": provider,
            "state": internal_state,
        }
        return _redirect_to_provider(provider, provider_config, session_data, is_https, signer)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Device verification failed for user_code=%s", user_code)
        raise HTTPException(status_code=500, detail="Internal server error")


async def _parse_device_token_params(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):
        body = await request.json()

        params = {
            "grant_type": body.get("grant_type"),
            "device_code": body.get("device_code"),
            "client_id": body.get("client_id"),
            "client_secret": body.get("client_secret"),
            "code": body.get("code"),
            "code_verifier": body.get("code_verifier"),
            "refresh_token": body.get("refresh_token"),
            "redirect_uri": body.get("redirect_uri"),
        }
    elif content_type.startswith("application/x-www-form-urlencoded"):
        form = await request.form()

        params = {
            "grant_type": form.get("grant_type"),
            "device_code": form.get("device_code"),
            "client_id": form.get("client_id"),
            "client_secret": form.get("client_secret"),
            "code": form.get("code"),
            "code_verifier": form.get("code_verifier"),
            "refresh_token": form.get("refresh_token"),
            "redirect_uri": form.get("redirect_uri"),
        }
    else:
        raise HTTPException(
            status_code=415, detail="content-type must be application/json or application/x-www-form-urlencoded"
        )

    if params.get("client_id"):
        return params

    request_redirect_uri = params.get("redirect_uri")
    if not isinstance(request_redirect_uri, str):
        return params

    try:
        hostname = (urlparse(request_redirect_uri).hostname or "").lower()
    except ValueError:
        return params

    auth_header = request.headers.get("authorization", "")
    scheme, _, encoded = auth_header.partition(" ")
    has_basic_credentials = scheme.lower() == "basic" and encoded

    is_quick_suite_host = hostname == "quicksight.aws.amazon.com" or hostname.endswith(".quicksight.aws.amazon.com")
    if not is_quick_suite_host:
        if has_basic_credentials:
            logger.warning(
                "client_secret_basic was provided for non-Quick Suite redirect_uri host '%s'; "
                "skipping Quick Suite fallback client_id parsing.",
                hostname or "unknown",
            )
        return params

    if not has_basic_credentials:
        return params

    try:
        logger.info("Quick Suite host identified. Attempting to resolve client credentials from Authorization header.")
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        basic_client_id, basic_client_secret = decoded.split(":", 1)
    except Exception as e:
        logger.warning(
            f"Quick Suite Authorization header parsing failed: {e}. Continuing without fallback credentials."
        )
        return params

    if basic_client_id:
        params["client_id"] = basic_client_id
        params["client_secret"] = basic_client_secret
        logger.info("Resolved Quick Suite client credentials from Authorization header.")

    return params


@router.post("/oauth2/token", response_model=DeviceTokenResponse, response_model_exclude_none=True)
async def device_token(
    request: Request,
    user_service: UserService = Depends(get_user_service),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
    consent_store: ConsentStore = Depends(get_consent_store),
):
    try:
        return await _device_token_handler(request, user_service, store, consent_store)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Token endpoint failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def _device_token_handler(
    request: Request,
    user_service: UserService,
    store: OAuthStateStoreProtocol,
    consent_store: ConsentStore,
) -> DeviceTokenResponse | JSONResponse:
    params = await _parse_device_token_params(request)
    grant_type: str | None = params["grant_type"]
    device_code: str | None = params["device_code"]
    client_id: str | None = params["client_id"]
    client_secret: str | None = params["client_secret"]
    code: str | None = params["code"]
    code_verifier: str | None = params["code_verifier"]
    refresh_token: str | None = params["refresh_token"]
    redirect_uri: str | None = params["redirect_uri"]

    if not grant_type:
        return oauth_error_response("invalid_request", "grant_type is required")
    if not client_id:
        return oauth_error_response("invalid_request", "client_id is required")

    logger.info("TOKEN ENDPOINT CALLED")
    logger.info(f"grant_type: {grant_type}")

    # Authorization Code Flow
    if grant_type == "authorization_code":
        if not code or not redirect_uri:
            return oauth_error_response("invalid_request", "code and redirect_uri are required")
        auth_code_data = store.get_authcode(code)
        if not auth_code_data:
            return oauth_error_response("invalid_grant", "authorization code not found or expired")
        if auth_code_data["client_id"] != client_id:
            return oauth_error_response("invalid_client", "client_id mismatch")
        if client_id == settings.registry_app_name and client_secret != settings.registry_client_secret:
            return oauth_error_response("invalid_client", "missing or invalid client_secret")
        if client_id != settings.registry_app_name and not store.validate_client_credentials(client_id, client_secret):
            return oauth_error_response("invalid_client", "invalid client credentials")
        if auth_code_data["redirect_uri"] != redirect_uri:
            return oauth_error_response("invalid_grant", "redirect_uri mismatch")
        current_time = int(time.time())
        if current_time > auth_code_data["expires_at"]:
            return oauth_error_response("invalid_grant", "authorization code expired")
        code_challenge = auth_code_data.get("code_challenge")
        if code_challenge:
            if not code_verifier:
                return oauth_error_response("invalid_request", "code_verifier required for PKCE")

            method = auth_code_data.get("code_challenge_method", "S256")
            # Compute challenge from verifier and compare with stored challenge
            if method != "S256":
                return oauth_error_response("invalid_request", "code_challenge_method must be S256")

            computed_challenge = create_s256_code_challenge(code_verifier)
            if computed_challenge != code_challenge:
                return oauth_error_response("invalid_grant", "code_verifier validation failed")

        auth_code_data = store.consume_authcode(code)
        if auth_code_data is None:
            return oauth_error_response("invalid_grant", "authorization code already used")

        user_info = auth_code_data["user_info"]

        # Use resolved scope from authorization code (negotiated in callback)
        # Fall back to computing from groups for backward compatibility with old codes
        resolved_scopes = auth_code_data.get("resolved_scope")
        if resolved_scopes is None:
            logger.info("No resolved_scope in auth code, computing from groups (backward compatibility)")
            user_groups = user_info.get("groups", [])
            resolved_scopes = (
                map_groups_to_scopes(user_groups, settings.scopes_file_config)
                if user_groups
                else user_info.get("scopes", [])
            )

        # Resolve user_id from MongoDB
        user_id = await user_service.resolve_user_id(user_info)

        scope_claim = " ".join(resolved_scopes) if isinstance(resolved_scopes, list) else resolved_scopes
        access_token = mint_managed_agent_token(
            JWT_TOKEN_CONFIG,
            subject=user_info["username"],
            client_id=client_id,
            expires_in_seconds=settings.oauth_access_token_expiry_seconds,
            iat=current_time,
            extra_claims={
                "name": user_info.get("name"),
                "idp_id": user_info.get("idp_id"),
                "user_id": user_id,
                "scope": scope_claim,
                "groups": user_info.get("groups", []),
                "token_use": "access",
                "auth_provider": settings.auth_provider,
            },
        )

        rt = secrets.token_urlsafe(32)
        refresh_expires_at = current_time + REFRESH_TOKEN_TTL_SECONDS
        store.save_refresh_token(
            rt,
            {
                "client_id": client_id,
                "user_info": user_info,
                "scope": scope_claim,
                "expires_at": refresh_expires_at,
            },
        )

        return DeviceTokenResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=settings.oauth_access_token_expiry_seconds,
            scope=scope_claim,
            refresh_token=rt,
        )

    elif grant_type == "urn:ietf:params:oauth:grant-type:device_code":
        if not device_code:
            return oauth_error_response("invalid_request", "device_code is required")
        device_data = store.get_device_code(device_code)
        if not device_data:
            return oauth_error_response("invalid_grant", "device_code not found")
        if device_data["client_id"] != client_id:
            return oauth_error_response("invalid_client", "client_id mismatch")
        if not store.validate_client_credentials(client_id, client_secret):
            return oauth_error_response("invalid_client", "invalid client credentials")
        current_time = int(time.time())
        if current_time > device_data["expires_at"]:
            return oauth_error_response("expired_token", "device_code has expired")
        if device_data["status"] == "pending":
            return oauth_error_response("authorization_pending", "user has not yet authorized this request")
        if device_data["status"] == "denied":
            return oauth_error_response("access_denied", "user denied authorization")
        if device_data["status"] != "approved":
            return oauth_error_response("server_error", "unexpected server state", 500)

        # Atomically consume the device_code so a concurrent poll can't also mint from it.
        device_data = store.consume_device_code(device_code)
        if device_data is None:
            return oauth_error_response("invalid_grant", "device_code already used")

        mapped_user = device_data["mapped_user"]
        resolved_scopes = device_data["resolved_scope"]
        if not isinstance(mapped_user, dict) or resolved_scopes is None:
            return oauth_error_response("server_error", "approved device code is missing user context", 500)

        user_id = await user_service.resolve_user_id(mapped_user)
        scope_claim = " ".join(resolved_scopes) if isinstance(resolved_scopes, list) else resolved_scopes

        access_token = mint_managed_agent_token(
            JWT_TOKEN_CONFIG,
            subject=mapped_user["username"],
            client_id=client_id,
            expires_in_seconds=settings.oauth_access_token_expiry_seconds,
            iat=current_time,
            extra_claims={
                "name": mapped_user.get("name"),
                "idp_id": mapped_user.get("idp_id"),
                "user_id": user_id,
                "scope": scope_claim,
                "groups": mapped_user.get("groups", []),
                "token_use": "access",
                "auth_provider": settings.auth_provider,
            },
        )
        rt = secrets.token_urlsafe(32)
        store.save_refresh_token(
            rt,
            {
                "client_id": client_id,
                "user_info": mapped_user,
                "scope": scope_claim,
                "expires_at": current_time + REFRESH_TOKEN_TTL_SECONDS,
            },
        )
        store.delete_user_code(device_data["user_code"])

        return DeviceTokenResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=settings.oauth_access_token_expiry_seconds,
            scope=scope_claim,
            refresh_token=rt,
        )

    elif grant_type == "refresh_token":
        if not refresh_token:
            return oauth_error_response("invalid_request", "refresh_token is required")
        refresh_token_data = store.get_refresh_token(refresh_token)
        if not refresh_token_data:
            return oauth_error_response("invalid_grant", "refresh token invalid or expired")
        if refresh_token_data.get("client_id") != client_id:
            return oauth_error_response("invalid_client", "client_id mismatch")
        if client_id == settings.registry_app_name and client_secret != settings.registry_client_secret:
            return oauth_error_response("invalid_client", "missing or invalid client_secret")
        if client_id != settings.registry_app_name and not store.validate_client_credentials(client_id, client_secret):
            return oauth_error_response("invalid_client", "invalid client credentials")

        user_info = refresh_token_data["user_info"]
        user_id = await user_service.resolve_user_id(user_info)
        if user_id and not _is_registry_client(client_id) and not consent_store.has_client_consent(user_id, client_id):
            return oauth_error_response(
                "invalid_grant",
                "User consent is required. Restart the authorization flow.",
            )

        now = int(time.time())
        new_refresh_token = secrets.token_urlsafe(32)
        new_refresh_data = {
            "client_id": client_id,
            "user_info": user_info,
            "scope": refresh_token_data.get("scope", ""),
            "expires_at": now + REFRESH_TOKEN_TTL_SECONDS,
        }
        rt_data = store.rotate_refresh_token(
            old_token=refresh_token,
            new_token=new_refresh_token,
            new_data=new_refresh_data,
        )
        if rt_data is None:
            return oauth_error_response("invalid_grant", "refresh token already used")

        access_token = mint_managed_agent_token(
            JWT_TOKEN_CONFIG,
            subject=user_info["username"],
            client_id=client_id,
            expires_in_seconds=settings.oauth_access_token_expiry_seconds,
            iat=now,
            extra_claims={
                "user_id": user_id,
                "scope": rt_data.get("scope", ""),
                "groups": user_info.get("groups", []),
                "token_use": "access",
                "auth_provider": settings.auth_provider,
            },
        )

        logger.info(f"Rotated refresh token for user: {user_info['username']}")

        return DeviceTokenResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=settings.oauth_access_token_expiry_seconds,
            scope=rt_data.get("scope", ""),
            refresh_token=new_refresh_token,
        )

    return oauth_error_response("unsupported_grant_type", f"grant_type '{grant_type}' is not supported")


@router.get("/oauth2/providers")
async def get_oauth2_providers(oauth2_config: OAuth2Config = Depends(get_oauth2_config)):
    try:
        enabled = []
        for provider_name, config in cast(dict[str, AuthProviderConfig], oauth2_config["providers"]).items():
            # Always return one provider that is both enabled and matches that AUTH_PROVIDER env var.
            if config.get("enabled", False) and provider_name == settings.auth_provider:
                enabled.append(
                    {"name": provider_name, "display_name": config.get("display_name", provider_name.title())}
                )
        return {"providers": enabled}
    except Exception as e:
        logger.error(f"Error getting OAuth2 providers: {e}")
        return {"providers": [], "error": str(e)}


@router.get("/oauth2/login/{provider}")
async def oauth2_login(
    provider: AllowedProvider,
    response_type: str,
    client_id: str,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    redirect_uri: str | None = None,
    resource: str | None = None,
    state: str | None = None,
    scope: str | None = None,
    oauth2_config: OAuth2Config = Depends(get_oauth2_config),
    signer: URLSafeTimedSerializer = Depends(get_signer),
    is_https: bool = Depends(check_if_https),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
):
    error_url = settings.registry_error_redirect
    try:
        provider_config = oauth2_config["providers"][provider]
        if not provider_config.get("enabled", False):
            return JSONResponse({"detail": f"Provider {provider} is disabled"}, 400)

        if response_type != "code":
            params = {"error": "unsupported_response_type", "error_description": "only supports response_type=code"}
            return RedirectResponse(f"{error_url}?{urlencode(params)}", 302)

        if redirect_uri is None or code_challenge is None or code_challenge_method is None:
            params = {
                "error": "invalid_request",
                "error_description": "redirect_uri, code_challenge and code_challenge_method are all required",
            }
            return RedirectResponse(f"{error_url}?{urlencode(params)}", 302)

        client_error = _validate_known_client_for_redirect(client_id, redirect_uri, store)
        if client_error is not None:
            return client_error

        if code_challenge_method != "S256":
            params = {
                "error": "invalid_request",
                "error_description": "code_challenge_method must be S256",
            }
            return RedirectResponse(f"{error_url}?{urlencode(params)}", 302)

        internal_state_data = {"nonce": secrets.token_urlsafe(24), "client_state": state}
        internal_state = base64.urlsafe_b64encode(json.dumps(internal_state_data).encode("utf-8")).decode().rstrip("=")

        session_data = {
            "state": internal_state,
            "client_state": state,
            "provider": provider,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        }
        if resource:
            session_data["resource"] = resource
        if scope:
            session_data["requested_scope"] = scope

        return _redirect_to_provider(provider, provider_config, session_data, is_https, signer)
    except Exception:
        logger.exception(f"Error initiating OAuth2 login for {provider}")

        return RedirectResponse(url=f"{error_url}?error=server_error", status_code=302)


@router.get("/oauth2/consent", response_class=HTMLResponse)
async def consent_page(
    nonce: str | None = None,
    oauth2_consent_nonce: str | None = Cookie(None, alias=settings.oauth2_consent_nonce_cookie_name),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> HTMLResponse:
    try:
        if not nonce or not oauth2_consent_nonce or not hmac.compare_digest(oauth2_consent_nonce, nonce):
            return HTMLResponse(render_consent_error_page(), status_code=400)

        pending = pending_store.peek(oauth2_consent_nonce)
        if pending is None:
            return HTMLResponse(render_consent_error_page(), status_code=400)

        client_id = pending["session_data"]["client_id"]
        client_metadata = store.get_client(client_id) or {}

        return HTMLResponse(
            render_consent_page(
                client_name=client_metadata.get("client_name", "Unknown application"),
                client_uri=client_metadata.get("client_uri"),
                ip_address=client_metadata.get("ip_address"),
                registered_at=client_metadata.get("registered_at"),
                nonce=oauth2_consent_nonce,
                approve_action=_auth_server_route_path("/oauth2/consent/approve"),
                deny_action=_auth_server_route_path("/oauth2/consent/deny"),
            )
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error rendering OAuth2 consent page")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/oauth2/consent/approve")
async def approve_consent(
    nonce: str = Form(...),
    oauth2_consent_nonce: str | None = Cookie(None, alias=settings.oauth2_consent_nonce_cookie_name),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
    consent_store: ConsentStore = Depends(get_consent_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
):
    try:
        if not oauth2_consent_nonce or not hmac.compare_digest(oauth2_consent_nonce, nonce):
            return JSONResponse({"detail": "Invalid or expired consent request"}, status_code=400)

        pending = pending_store.consume(nonce)
        if pending is None:
            return JSONResponse(
                {"detail": "This consent link has expired. Please retry from your MCP client."},
                status_code=400,
            )

        mapped_user = pending["mapped_user"]
        session_data = pending["session_data"]
        user_id = mapped_user["user_id"]
        client_id = session_data["client_id"]

        consent_store.grant_client_consent(user_id, client_id)

        if pending.get("flow_type") == "device":
            response = _finish_device_callback(
                pending["device_code"],
                pending["mapped_user"],
                pending["resolved_scopes"],
                store,
            )
            response.delete_cookie(settings.oauth2_consent_nonce_cookie_name)
            return response

        response = _finish_oauth2_callback(
            pending["token_data"],
            mapped_user,
            session_data,
            pending["resolved_scopes"],
            store,
        )
        response.delete_cookie(settings.oauth2_consent_nonce_cookie_name)
        return response
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error approving OAuth2 consent")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/oauth2/consent/deny")
async def deny_consent(
    nonce: str = Form(...),
    oauth2_consent_nonce: str | None = Cookie(None, alias=settings.oauth2_consent_nonce_cookie_name),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> Response:
    try:
        if not oauth2_consent_nonce or not hmac.compare_digest(oauth2_consent_nonce, nonce):
            return JSONResponse({"detail": "Invalid or expired consent request"}, status_code=400)

        pending = pending_store.consume(nonce)
        if pending and pending.get("flow_type") == "device":
            response = _finish_device_denial(pending["device_code"], store)
            response.delete_cookie(settings.oauth2_consent_nonce_cookie_name)
            return response

        params = {"error": "access_denied", "error_description": "User denied the authorization request"}
        response = RedirectResponse(url=f"{settings.registry_error_redirect}?{urlencode(params)}", status_code=302)
        response.delete_cookie(settings.oauth2_consent_nonce_cookie_name)
        return response
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error denying OAuth2 consent")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/oauth2/callback/{provider}")
async def oauth2_callback(
    provider: AllowedProvider,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    oauth2_temp_session: str | None = Cookie(None, alias=settings.oauth2_temp_session_cookie_name),
    oauth2_config: OAuth2Config = Depends(get_oauth2_config),
    user_service: UserService = Depends(get_user_service),
    signer: URLSafeTimedSerializer = Depends(get_signer),
    auth_provider: AuthProvider = Depends(get_auth_provider),
    store: OAuthStateStoreProtocol = Depends(get_oauth_state_store),
    consent_store: ConsentStore = Depends(get_consent_store),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
    is_https: bool = Depends(check_if_https),
):
    error_url = settings.registry_error_redirect

    try:
        if error is not None:
            logger.error(f"OAuth2 error from {provider}: {error}")

            return RedirectResponse(url=f"{error_url}?error=oauth2_error&details={error}", status_code=302)

        if code is None or state is None or oauth2_temp_session is None:
            return JSONResponse({"detail": "Missing required OAuth2 parameters"}, 400)

        # Validate temporary session
        try:
            session_data = signer.loads(oauth2_temp_session, max_age=settings.oauth_session_ttl_seconds)
        except (SignatureExpired, BadSignature):
            return JSONResponse(
                status_code=401,
                content={"detail": "OAuth session expired"},
                headers={"WWW-Authenticate": f'Bearer realm="{settings.jarvis_realm}"'},
            )

        # Decode internal state from temp session to compare client_state
        internal_state = session_data.get("state")

        if state != internal_state:
            return JSONResponse({"detail": "Invalid state parameter"}, 400)

        if provider != session_data.get("provider"):
            return JSONResponse({"detail": "Provider mismatch"}, 400)

        provider_config = oauth2_config["providers"][provider]

        callback_uri = _auth_server_external_url(f"/oauth2/callback/{provider}")
        token_data = await exchange_code_for_token(provider, code, provider_config, callback_uri)

        # Extract user information from tokens or userinfo
        mapped_user: dict[str, Any] | None = None
        try:
            if provider in ["cognito", "keycloak"]:
                if "id_token" in token_data:
                    id_claims = await _decode_oidc_provider_token(token_data["id_token"], provider, auth_provider)
                    mapped_user = {
                        "username": id_claims.get("preferred_username") or id_claims.get("sub"),
                        "email": id_claims.get("email"),
                        "name": id_claims.get("name") or id_claims.get("given_name"),
                        "idp_id": id_claims.get("sub"),
                        "groups": id_claims.get("groups", []),
                    }
                elif "access_token" in token_data:
                    access_claims = await _decode_oidc_provider_token(
                        token_data["access_token"], provider, auth_provider
                    )
                    mapped_user = {
                        "username": access_claims.get("username") or access_claims.get("sub"),
                        "email": access_claims.get("email"),
                        "name": access_claims.get("name"),
                        "idp_id": access_claims.get("sub"),
                        "groups": access_claims.get("groups", []),
                    }
                else:
                    raise ValueError("No ID token and access token claims unavailable")
            elif provider == "entra":
                user_info = await auth_provider.get_user_info(
                    access_token=token_data["access_token"], id_token=token_data.get("id_token")
                )
                mapped_user = {
                    "username": user_info.get("username"),
                    "email": user_info.get("email"),
                    "name": user_info.get("name"),
                    "idp_id": user_info.get("id"),
                    "groups": user_info.get("groups", []),
                }
            else:
                raise ValueError(f"Unsupported provider {provider}")
        except (InvalidSignatureError, InvalidTokenError, InvalidIssuerError, InvalidAudienceError):
            raise
        except Exception:
            logger.exception("Falling back to userInfo on token parsing error")

            user_info = await get_user_info(token_data["access_token"], provider_config)

            mapped_user = map_user_info(user_info, provider_config)

        # Resolve user_id from MongoDB and add to mapped_user
        user_id = await user_service.resolve_user_id(mapped_user)
        if user_id:
            mapped_user["user_id"] = user_id
            logger.debug(f"Added user_id {user_id} to mapped_user")

        mapped_user["provider"] = provider

        is_device_flow = session_data.get("flow_type") == "device"
        device_code = session_data.get("device_code") if is_device_flow else None
        device_data = store.get_device_code(device_code) if isinstance(device_code, str) else None
        if is_device_flow and (device_data is None or device_data["status"] != "pending"):
            response = HTMLResponse(render_device_link_error_page(), status_code=400)
            response.delete_cookie(settings.oauth2_temp_session_cookie_name)
            return response

        # Resolve scope: intersection of requested scope and user's default scope
        user_groups = mapped_user.get("groups", [])
        default_user_scopes = (
            map_groups_to_scopes(user_groups, settings.scopes_file_config)
            if user_groups
            else mapped_user.get("scopes", [])
        )

        requested_scope_str = device_data.get("scope") if device_data else session_data.get("requested_scope")
        if requested_scope_str:
            # Client requested specific scopes, compute intersection
            requested_scopes = requested_scope_str.split()
            resolved_scopes = [s for s in requested_scopes if s in default_user_scopes]

            if not resolved_scopes:
                # Intersection is empty, return error
                logger.warning(
                    f"Scope negotiation failed for user {mapped_user['username']}: "
                    f"requested={requested_scopes}, available={default_user_scopes}"
                )
                if is_device_flow:
                    response = HTMLResponse(render_device_link_error_page(), status_code=400)
                    response.delete_cookie(settings.oauth2_temp_session_cookie_name)
                    return response

                error_params = {
                    "error": "invalid_scope",
                    "error_description": "Requested scopes are not available for this user",
                }
                client_state = session_data.get("client_state")
                if client_state:
                    error_params["state"] = client_state

                client_redirect_uri = session_data["client_redirect_uri"]
                redirect_url = f"{client_redirect_uri}?{urlencode(error_params)}"
                response = RedirectResponse(url=redirect_url, status_code=302)
                response.delete_cookie(settings.oauth2_temp_session_cookie_name)
                return response

            logger.info(
                f"Scope negotiation successful: requested={requested_scopes}, "
                f"available={default_user_scopes}, resolved={resolved_scopes}"
            )
        else:
            # Client did not request specific scopes, use default user scopes
            resolved_scopes = default_user_scopes
            logger.info(f"No scope requested, using default user scopes: {resolved_scopes}")

        if is_device_flow:
            assert isinstance(device_code, str)
            assert device_data is not None
            client_id = device_data["client_id"]
            user_id = mapped_user.get("user_id")
            if (
                user_id
                and not _is_registry_client(client_id)
                and not consent_store.has_client_consent(user_id, client_id)
            ):
                return _redirect_to_pending_consent(
                    {
                        "flow_type": "device",
                        "device_code": device_code,
                        "mapped_user": mapped_user,
                        "resolved_scopes": resolved_scopes,
                        "session_data": {"client_id": client_id},
                    },
                    settings.device_code_expiry_seconds,
                    is_https,
                    pending_store,
                )

            response = _finish_device_callback(device_code, mapped_user, resolved_scopes, store)
            response.delete_cookie(settings.oauth2_temp_session_cookie_name)
            return response

        client_id = session_data["client_id"]
        user_id = mapped_user.get("user_id")
        if user_id and not _is_registry_client(client_id) and not consent_store.has_client_consent(user_id, client_id):
            return _redirect_to_pending_consent(
                {
                    "token_data": token_data,
                    "mapped_user": mapped_user,
                    "session_data": session_data,
                    "resolved_scopes": resolved_scopes,
                },
                PENDING_CONSENT_TTL_SECONDS,
                is_https,
                pending_store,
            )

        return _finish_oauth2_callback(token_data, mapped_user, session_data, resolved_scopes, store)

    except Exception:
        logger.exception(f"Error in OAuth2 callback for {provider}")

        return RedirectResponse(url=f"{error_url}?error=oauth2_callback_failed", status_code=302)


async def exchange_code_for_token(
    provider: AllowedProvider,
    code: str,
    provider_config: AuthProviderConfig | EntraConfig,
    callback_uri: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        token_data = {
            "grant_type": provider_config["grant_type"],
            "client_id": provider_config["client_id"],
            "client_secret": provider_config["client_secret"],
            "code": code,
            "redirect_uri": callback_uri,
        }
        headers = {"Accept": "application/json"}
        response = await client.post(provider_config["token_url"], data=token_data, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_user_info(access_token: str, provider_config: AuthProviderConfig | EntraConfig) -> dict:
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = await client.get(provider_config["user_info_url"], headers=headers)
        response.raise_for_status()
        return response.json()


def map_user_info(user_info: dict, provider_config: AuthProviderConfig | EntraConfig) -> dict:
    """Map user info from OAuth provider to standard format.

    Args:
        user_info: Raw user info from provider's userinfo endpoint
        provider_config: Provider configuration with claim mappings

    Returns:
        Standardized user info dict with username, email, name, user_id, and groups
    """
    mapped: dict[str, Any] = {
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
async def oauth2_logout(
    provider: AllowedProvider, redirect_uri: str | None = None, oauth2_config: OAuth2Config = Depends(get_oauth2_config)
):
    redirect_uri = redirect_uri or f"{settings.registry_client_url}/login"

    try:
        provider_config = oauth2_config["providers"][provider]

        logout_url = provider_config["logout_url"]

        logout_params = {"client_id": provider_config["client_id"], "post_logout_redirect_uri": redirect_uri}

        logout_redirect_url = f"{logout_url}?{urlencode(logout_params)}"

        return RedirectResponse(url=logout_redirect_url, status_code=302)
    except Exception:
        logger.exception(f"Error initiating logout for {provider}")

        return RedirectResponse(url=redirect_uri, status_code=302)


@router.get("/validate")
async def validate_request(
    request: Request,
    validator: SimplifiedCognitoValidator = Depends(get_validator),
    signer: URLSafeTimedSerializer = Depends(get_signer),
    auth_provider: AuthProvider = Depends(get_auth_provider),
):
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
                    validation_result = validate_session_cookie(cookie_value, signer=signer)
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
                    unverified_claims = decode_jwt_unverified(access_token)
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
                    logger.info(f"Using authentication provider: {auth_provider.__class__.__name__}")

                    # Provider-specific validation
                    if hasattr(auth_provider, "validate_token"):
                        # For Keycloak, Entra ID, etc. - no additional headers needed
                        validation_result = await auth_provider.validate_token(access_token)
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
                        validation_result = await validator.validate_token(
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
            user_scopes = map_groups_to_scopes(user_groups, settings.scopes_file_config)
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


def validate_session_cookie(cookie_value: str, *, signer) -> dict[str, Any]:
    """
    Validate session cookie using itsdangerous serializer.

    Args:
        cookie_value: The session cookie value

    Returns:
        Dict containing validation results matching JWT validation format
    Raises:
        ValueError: If cookie is invalid or expired
    """
    try:
        # Decrypt cookie (max_age=28800 for 8 hours)
        data = signer.loads(cookie_value, max_age=28800)

        # Extract user info
        username = data.get("username")
        groups = data.get("groups", [])

        # Map groups to scopes using global settings.scopes_config
        scopes = map_groups_to_scopes(groups, settings.scopes_file_config)

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
