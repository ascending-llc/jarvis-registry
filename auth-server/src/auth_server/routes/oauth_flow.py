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
from typing import Any, NamedTuple, cast
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from registry_pkgs.core.client_categories import (
    AUTHORIZATION_CODE_GRANT_TYPE,
    ClientCategory,
    resolve_granted_scopes,
)
from registry_pkgs.core.consent_store import PENDING_CONSENT_TTL_SECONDS, ConsentStore, PendingConsentStore
from registry_pkgs.core.downstream_oauth import (
    DEVICE_CODE_GRANT_TYPE,
    generate_user_code,
    normalize_user_code,
    oauth_error_payload,
)
from registry_pkgs.core.jwt_utils import (
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    decode_jwt_with_jwk,
    find_matching_jwk,
    get_token_kid,
)
from registry_pkgs.core.oauth_state_store import OAuthStateStoreProtocol
from registry_pkgs.core.redirect_uri import (
    VENDOR_BROKER_REDIRECT_URIS,
    build_oauth_error_redirect_url,
    is_safe_unverified_redirect_target,
    redirect_uri_matches,
)
from registry_pkgs.core.scopes import get_scope_description, map_groups_to_scopes

from ..core.config import settings
from ..core.types import AllowedProvider, AuthProviderConfig, EntraConfig, OAuth2Config
from ..deps import (
    check_if_https,
    get_auth_provider,
    get_client_registration_service,
    get_consent_store,
    get_oauth2_config,
    get_oauth_state_store,
    get_pending_consent_store,
    get_signer,
    get_token_grant_service,
    get_user_service,
)
from ..models.client_registration import ClientRegistrationRequest, ClientRegistrationResponse
from ..models.device_flow import DeviceCodeResponse, DeviceTokenResponse
from ..providers.base import AuthProvider
from ..services.client_registration_service import ClientRegistrationError, ClientRegistrationService
from ..services.oauth_client_policy import (
    is_registry_client as _is_registry_client,
)
from ..services.oauth_client_policy import (
    resolve_client_metadata as _resolve_client_metadata,
)
from ..services.token_grant_service import TokenGrantService, validate_token_grant_request
from ..services.user_service import UserService
from .consent_templates import (
    render_consent_error_page,
    render_consent_page,
    render_device_approved_page,
    render_device_code_confirm_page,
    render_device_code_entry_page,
    render_device_denied_page,
    render_device_link_error_page,
    render_device_scope_error_page,
    render_device_server_error_page,
    render_redirect_error_consent_page,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# JWT / signer configuration (use settings)
# All access tokens issued by /oauth2/token (+ device flow) are managed-agent (proxy) tokens.
JWT_TOKEN_CONFIG = settings.jwt_token_config

_OIDC_TOKEN_ALGORITHMS = ["RS256"]
_REDIRECT_ERROR_CONSENT_FLOW_TYPE = "redirect_error"


class _RedirectValidationError(NamedTuple):
    error: str
    error_description: str


def _trusted_error_redirect_uris() -> frozenset[str]:
    """Return exact redirect targets trusted independently of DCR client metadata."""
    deployment_callback = f"{settings.jwt_issuer}/api/mcp/jarvis_registry/oauth/callback"
    return VENDOR_BROKER_REDIRECT_URIS | {deployment_callback}


def _peek_redirect_error_consent(
    pending_store: PendingConsentStore,
    nonce: str,
) -> dict[str, Any] | None:
    """Return a pending redirect-error request without accepting another consent payload type."""
    pending = pending_store.peek(nonce)
    if pending is None or pending.get("flow_type") != _REDIRECT_ERROR_CONSENT_FLOW_TYPE:
        return None
    return pending


def _consume_redirect_error_consent(
    pending_store: PendingConsentStore,
    nonce: str,
) -> dict[str, Any] | None:
    """Atomically consume a redirect-error request after verifying its payload type."""
    if _peek_redirect_error_consent(pending_store, nonce) is None:
        return None

    consumed = pending_store.consume(nonce)
    if consumed is None or consumed.get("flow_type") != _REDIRECT_ERROR_CONSENT_FLOW_TYPE:
        return None
    return consumed


def _peek_authorization_consent(
    pending_store: PendingConsentStore,
    nonce: str,
) -> dict[str, Any] | None:
    """Return a pending authorization-consent request, rejecting the redirect-error payload type."""
    pending = pending_store.peek(nonce)
    if pending is None or pending.get("flow_type") == _REDIRECT_ERROR_CONSENT_FLOW_TYPE:
        return None
    return pending


def _consume_authorization_consent(
    pending_store: PendingConsentStore,
    nonce: str,
) -> dict[str, Any] | None:
    """Atomically consume a pending authorization-consent request, rejecting the redirect-error payload type."""
    if _peek_authorization_consent(pending_store, nonce) is None:
        return None

    consumed = pending_store.consume(nonce)
    if consumed is None or consumed.get("flow_type") == _REDIRECT_ERROR_CONSENT_FLOW_TYPE:
        return None
    return consumed


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


def _is_registered_redirect_uri(client_metadata: dict[str, Any], redirect_uri: str) -> bool:
    registered_redirect_uris = client_metadata.get("redirect_uris") or []
    return any(redirect_uri_matches(redirect_uri, registered) for registered in registered_redirect_uris)


def _get_unknown_client_response() -> JSONResponse:
    return oauth_error_response("invalid_client", "Unknown client_id")


def _validate_known_client_for_redirect(
    client_id: str,
    redirect_uri: str,
    store: OAuthStateStoreProtocol,
) -> _RedirectValidationError | None:
    if _is_registry_client(client_id):
        return None

    client_metadata = store.get_client(client_id)
    if client_metadata is None:
        return _RedirectValidationError("invalid_client", "Unknown client_id")

    if not _is_registered_redirect_uri(client_metadata, redirect_uri):
        return _RedirectValidationError("invalid_request", "redirect_uri is not registered for this client")

    if AUTHORIZATION_CODE_GRANT_TYPE not in (client_metadata.get("grant_types") or []):
        return _RedirectValidationError(
            "unauthorized_client",
            f"client is not authorized for {AUTHORIZATION_CODE_GRANT_TYPE}",
        )

    return None


def _validate_known_client(
    client_id: str,
    store: OAuthStateStoreProtocol,
) -> JSONResponse | None:
    if _is_registry_client(client_id):
        return None

    if _resolve_client_metadata(client_id, store) is None:
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
    consent_path: str = "/oauth2/consent",
) -> RedirectResponse:
    """Save a pending-consent nonce and 302 to /oauth2/consent; shared tail of the device-flow and
    Authorization-Code-Grant consent detours in oauth2_callback — the only difference between
    callers is what they put in pending_payload and how long the detour should live for.
    """
    nonce = secrets.token_urlsafe(32)
    pending_store.save(nonce, pending_payload, ttl_seconds=ttl_seconds)

    consent_url = f"{_auth_server_external_url(consent_path)}?nonce={nonce}"
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


def _redirect_error_to_client(
    redirect_uri: str,
    error: str,
    error_description: str,
    client_state: str | None,
) -> RedirectResponse:
    """Redirect an OAuth error to a redirect URI already established as safe."""
    return RedirectResponse(
        url=build_oauth_error_redirect_url(
            redirect_uri,
            error,
            error_description,
            client_state,
        ),
        status_code=302,
    )


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


def _finish_device_failure(
    device_code: str,
    status: str,
    store: OAuthStateStoreProtocol,
    *,
    error_description: str | None = None,
) -> None:
    """Mark a device code as terminally failed so token polling returns the real error."""
    device_data = store.get_device_code(device_code)
    if device_data is None or device_data["status"] != "pending":
        return

    updated = dict(device_data)
    updated["status"] = status
    if error_description is not None:
        updated["error_description"] = error_description
    store.update_device_code(device_code, updated)


def _finish_device_denial(device_code: str, store: OAuthStateStoreProtocol) -> HTMLResponse:
    _finish_device_failure(device_code, "denied", store)
    return HTMLResponse(render_device_denied_page())


def _register_client_common(
    registration: ClientRegistrationRequest,
    request: Request,
    client_registration_service: ClientRegistrationService,
    *,
    category: ClientCategory,
    default_client_name: str,
) -> ClientRegistrationResponse | JSONResponse:
    try:
        logger.info(
            f"incoming DCR request. client_name: {registration.client_name}, grant_types: {registration.grant_types}, "
            f"response_types: {registration.response_types}, scope: {registration.scope}, "
            f"token_endpoint_auth_method: {registration.token_endpoint_auth_method}."
        )

        return client_registration_service.register(
            registration,
            category=category,
            default_client_name=default_client_name,
            ip_address=request.client.host if request.client else "unknown",
        )
    except ClientRegistrationError as e:
        return oauth_error_response(e.error, e.description)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Client registration failed")
        raise HTTPException(status_code=500, detail="Client registration failed") from e


@router.post("/oauth2/register", response_model=ClientRegistrationResponse, response_model_exclude_none=True)
async def register_client(
    registration: ClientRegistrationRequest,
    request: Request,
    client_registration_service: ClientRegistrationService = Depends(get_client_registration_service),
) -> ClientRegistrationResponse | JSONResponse:
    return _register_client_common(
        registration,
        request,
        client_registration_service,
        category=ClientCategory.MCP_DCR,
        default_client_name="MCP Client",
    )


@router.post("/oauth2/register/a2a", response_model=ClientRegistrationResponse, response_model_exclude_none=True)
async def register_a2a_client(
    registration: ClientRegistrationRequest,
    request: Request,
    client_registration_service: ClientRegistrationService = Depends(get_client_registration_service),
) -> ClientRegistrationResponse | JSONResponse:
    return _register_client_common(
        registration,
        request,
        client_registration_service,
        category=ClientCategory.A2A_DCR,
        default_client_name="A2A Client",
    )


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

        client_metadata = _resolve_client_metadata(client_id, store) or {}
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

    normalized = normalize_user_code(user_code)
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
        normalized = normalize_user_code(user_code)
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


async def _parse_device_token_params(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            return {"_invalid_request": "JSON body is malformed"}
        if not isinstance(body, dict):
            return {"_invalid_request": "JSON body must be an object"}

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
    token_grant_service: TokenGrantService = Depends(get_token_grant_service),
):
    try:
        return await _device_token_handler(request, token_grant_service)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Token endpoint failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def _device_token_handler(
    request: Request,
    token_grant_service: TokenGrantService,
) -> DeviceTokenResponse | JSONResponse:
    params = await _parse_device_token_params(request)
    invalid_request = params.pop("_invalid_request", None)
    if isinstance(invalid_request, str):
        return oauth_error_response("invalid_request", invalid_request)

    token_request = validate_token_grant_request(params)
    if isinstance(token_request, JSONResponse):
        return token_request
    return await token_grant_service.exchange(token_request)


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
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
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
            if is_safe_unverified_redirect_target(redirect_uri, _trusted_error_redirect_uris()):
                return _redirect_to_pending_consent(
                    {
                        "flow_type": _REDIRECT_ERROR_CONSENT_FLOW_TYPE,
                        "redirect_uri": redirect_uri,
                        "error": client_error.error,
                        "error_description": client_error.error_description,
                        "client_state": state,
                    },
                    PENDING_CONSENT_TTL_SECONDS,
                    is_https,
                    pending_store,
                    consent_path="/oauth2/redirect-error-consent",
                )
            return oauth_error_response(client_error.error, client_error.error_description)

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

        pending = _peek_authorization_consent(pending_store, oauth2_consent_nonce)
        if pending is None:
            return HTMLResponse(render_consent_error_page(), status_code=400)

        client_id = pending["session_data"]["client_id"]
        client_metadata = _resolve_client_metadata(client_id, store) or {}
        granted_scopes = resolve_granted_scopes(
            client_id,
            pending.get("resolved_scopes") or [],
            settings.jwt_token_config,
        )
        scopes = [(name, get_scope_description(name, settings.scopes_file_config)) for name in granted_scopes]

        return HTMLResponse(
            render_consent_page(
                client_name=client_metadata.get("client_name", "Unknown application"),
                client_uri=client_metadata.get("client_uri"),
                redirect_uri=pending["session_data"].get("client_redirect_uri"),
                ip_address=client_metadata.get("ip_address"),
                registered_at=client_metadata.get("registered_at"),
                scopes=scopes,
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

        pending = _consume_authorization_consent(pending_store, nonce)
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

        pending = _consume_authorization_consent(pending_store, nonce)
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


@router.get("/oauth2/redirect-error-consent", response_class=HTMLResponse)
async def redirect_error_consent_page(
    nonce: str | None = None,
    oauth2_consent_nonce: str | None = Cookie(None, alias=settings.oauth2_consent_nonce_cookie_name),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> HTMLResponse:
    try:
        if not nonce or not oauth2_consent_nonce or not hmac.compare_digest(oauth2_consent_nonce, nonce):
            return HTMLResponse(render_consent_error_page(), status_code=400)

        pending = _peek_redirect_error_consent(pending_store, oauth2_consent_nonce)
        if pending is None:
            return HTMLResponse(render_consent_error_page(), status_code=400)

        return HTMLResponse(
            render_redirect_error_consent_page(
                redirect_uri=pending["redirect_uri"],
                error=pending["error"],
                error_description=pending["error_description"],
                nonce=oauth2_consent_nonce,
                approve_action=_auth_server_route_path("/oauth2/redirect-error-consent/approve"),
                deny_action=_auth_server_route_path("/oauth2/redirect-error-consent/deny"),
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error rendering redirect-error consent page")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/oauth2/redirect-error-consent/approve")
async def approve_redirect_error_consent(
    nonce: str = Form(...),
    oauth2_consent_nonce: str | None = Cookie(None, alias=settings.oauth2_consent_nonce_cookie_name),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> Response:
    try:
        if not oauth2_consent_nonce or not hmac.compare_digest(oauth2_consent_nonce, nonce):
            return JSONResponse({"detail": "Invalid or expired consent request"}, status_code=400)

        pending = _consume_redirect_error_consent(pending_store, nonce)
        if pending is None:
            return JSONResponse(
                {"detail": "This link has expired. Please retry from your MCP client."},
                status_code=400,
            )

        response = _redirect_error_to_client(
            pending["redirect_uri"],
            pending["error"],
            pending["error_description"],
            pending.get("client_state"),
        )
        response.delete_cookie(settings.oauth2_consent_nonce_cookie_name)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error approving redirect-error consent")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/oauth2/redirect-error-consent/deny")
async def deny_redirect_error_consent(
    nonce: str = Form(...),
    oauth2_consent_nonce: str | None = Cookie(None, alias=settings.oauth2_consent_nonce_cookie_name),
    pending_store: PendingConsentStore = Depends(get_pending_consent_store),
) -> Response:
    try:
        if not oauth2_consent_nonce or not hmac.compare_digest(oauth2_consent_nonce, nonce):
            return JSONResponse({"detail": "Invalid or expired consent request"}, status_code=400)

        pending = _consume_redirect_error_consent(pending_store, nonce)
        if pending is None:
            return JSONResponse(
                {"detail": "This link has expired. Please retry from your MCP client."},
                status_code=400,
            )

        params = {
            "error": "access_denied",
            "error_description": "User declined to relay this error to the MCP client",
        }
        response = RedirectResponse(
            url=f"{settings.registry_error_redirect}?{urlencode(params)}",
            status_code=302,
        )
        response.delete_cookie(settings.oauth2_consent_nonce_cookie_name)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error denying redirect-error consent")
        raise HTTPException(status_code=500, detail="Internal server error") from e


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
    is_device_flow = False
    device_code: str | None = None

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

        is_device_flow = session_data.get("flow_type") == "device"
        session_device_code = session_data.get("device_code")
        device_code = session_device_code if is_device_flow and isinstance(session_device_code, str) else None

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
                    if isinstance(device_code, str):
                        _finish_device_failure(
                            device_code,
                            "scope_denied",
                            store,
                            error_description="Requested scopes are not available for this user",
                        )
                    response = HTMLResponse(render_device_scope_error_page(), status_code=400)
                    response.delete_cookie(settings.oauth2_temp_session_cookie_name)
                    return response

                response = _redirect_error_to_client(
                    session_data["client_redirect_uri"],
                    "invalid_scope",
                    "Requested scopes are not available for this user",
                    session_data.get("client_state"),
                )
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
            if not isinstance(device_code, str) or device_data is None:
                response = HTMLResponse(render_device_link_error_page(), status_code=400)
                response.delete_cookie(settings.oauth2_temp_session_cookie_name)
                return response
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

        if is_device_flow and isinstance(device_code, str):
            try:
                _finish_device_failure(
                    device_code,
                    "failed",
                    store,
                    error_description="Unexpected error during sign-in",
                )
            except Exception:
                logger.exception("Failed to mark device_code as failed after callback error")
            response = HTMLResponse(render_device_server_error_page(), status_code=500)
            response.delete_cookie(settings.oauth2_temp_session_cookie_name)
            return response

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
