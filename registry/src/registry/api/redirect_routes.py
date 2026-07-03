import base64
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote, urlencode

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Cookie, Depends, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from registry_pkgs.core.jwt_utils import decode_jwt
from registry_pkgs.core.scopes import filter_known_groups, map_groups_to_scopes

from ..core.config import settings
from ..deps import check_if_https, get_user_service
from ..services.user_service import UserService
from ..utils.crypto_utils import (
    ABSOLUTE_SESSION_EXPIRES_SECONDS,
    REFRESH_TOKEN_EXPIRES_SECONDS,
    decrypt_value,
    encrypt_value,
    generate_access_token,
    generate_refresh_token,
    generate_token_pair,
    verify_refresh_token,
)
from ..utils.csrf import compute_csrf_token

logger = logging.getLogger(__name__)

router = APIRouter()

ACCESS_TOKEN_COOKIE_MAX_AGE_SECONDS = 86400


def _set_csrf_cookie(response: Response, access_token: str, cookie_secure: bool) -> None:
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=compute_csrf_token(access_token),
        max_age=ACCESS_TOKEN_COOKIE_MAX_AGE_SECONDS,
        httponly=False,
        samesite="lax",
        secure=cookie_secure,
        path="/",
    )


def _delete_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    response.delete_cookie(key=settings.refresh_cookie_name, path="/")
    response.delete_cookie(key=settings.csrf_cookie_name, path="/")


def _parse_session_started_at(raw_value: object, now: int) -> int | None:
    """Parse session_started_at from refresh token claims.

    None is grandfathered for pre-deploy refresh tokens. Invalid non-null values
    are rejected by returning None so the caller can fail closed with 401.
    """
    if raw_value is None:
        return now
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip().isdecimal():
        return int(raw_value)
    return None


def _get_required_string_claim(claims: dict, claim_name: str) -> str | None:
    value = claims.get(claim_name)
    if isinstance(value, str) and value.strip():
        return value
    return None


async def get_oauth2_providers():
    """Fetch available OAuth2 providers from auth server"""
    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"Fetching OAuth2 providers from {settings.auth_server_url}/oauth2/providers")
            response = await client.get(f"{settings.auth_server_url}/oauth2/providers", timeout=5.0)
            logger.info(f"OAuth2 providers response: status={response.status_code}")
            if response.status_code == 200:
                data = response.json()
                providers = data.get("providers", [])
                logger.info(f"Successfully fetched {len(providers)} OAuth2 providers: {providers}")
                return providers
            else:
                logger.warning(f"Auth server returned non-200 status: {response.status_code}, body: {response.text}")
    except Exception as e:
        logger.warning(f"Failed to fetch OAuth2 providers from auth server: {e}", exc_info=True)
    return []


# OAuth2 login redirect avoid /auth/ route collision with auth server
@router.get("/redirect/{provider}")
async def oauth2_login_redirect(provider: str, is_https: bool = Depends(check_if_https)):
    """Redirect to auth server for OAuth2 login"""
    try:
        # Registry backend receives `code` from auth-server, and calls the /token endpoint of auth-server.
        # Therefore the redirect URI should be a route on Registry backend.
        registry_url = settings.registry_url
        auth_external_url = settings.auth_server_external_url
        state_data = {"nonce": secrets.token_urlsafe(24)}
        client_state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = create_s256_code_challenge(code_verifier)
        auth_params = {
            "response_type": "code",
            "client_id": settings.registry_app_name,
            "redirect_uri": settings.registry_redirect_uri,
            "state": client_state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": registry_url,
        }
        auth_url = f"{auth_external_url}/oauth2/login/{provider}?{urlencode(auth_params)}"
        logger.info(f"registry_url: {registry_url}, auth_external_url: {auth_external_url}, auth_url: {auth_url}")
        logger.info(f"Redirecting to OAuth2 login for provider {provider}: {auth_url}")
        resp = RedirectResponse(url=auth_url, status_code=302)
        resp.set_cookie(
            key="registry_oauth2_code_verifier",
            value=encrypt_value(code_verifier),
            max_age=settings.oauth_session_ttl_seconds,
            httponly=True,
            secure=settings.session_cookie_secure and is_https,
            samesite="lax",
        )
        return resp
    except Exception as e:
        logger.error(f"Error redirecting to OAuth2 login for {provider}: {e}")
        return RedirectResponse(url="/login?error=oauth2_redirect_failed", status_code=302)


@router.get("/redirect")
async def oauth2_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    details: str | None = None,
    registry_oauth2_code_verifier: str | None = Cookie(None),
    user_service: UserService = Depends(get_user_service),
    is_https: bool = Depends(check_if_https),
):
    """Handle OAuth2 callback from auth server
    This endpoint receives an authorization code and exchanges it for a JWT access token.
    The user_id has already been resolved by auth_server from MongoDB and included in the JWT.
    """
    try:
        if error:
            logger.warning(f"OAuth2 callback received error: {error}, details: {details}")
            error_message = "Authentication failed"
            if error == "oauth2_error":
                error_message = f"OAuth2 provider error: {details}"
            elif error == "oauth2_init_failed":
                error_message = "Failed to initiate OAuth2 login"
            elif error == "oauth2_callback_failed":
                error_message = "OAuth2 authentication failed"

            return RedirectResponse(
                url=f"{settings.registry_client_url}/login?error={quote(error_message)}", status_code=302
            )

        if not code:
            logger.error("Missing authorization code in OAuth2 callback")
            return RedirectResponse(
                url=f"{settings.registry_client_url}/login?error=oauth2_missing_code", status_code=302
            )

        if registry_oauth2_code_verifier is None:
            return RedirectResponse(
                url=f"{settings.registry_client_url}/login?error=oauth2_missing_code_verifier", status_code=302
            )

        try:
            code_verifier = decrypt_value(registry_oauth2_code_verifier)
        except Exception:
            logger.exception("failed to decrypt registry_oauth2_code_verifier cookie.")

            return RedirectResponse(
                url=f"{settings.registry_client_url}/login?error=oauth2_missing_code_verifier", status_code=302
            )

        # Exchange authorization code for JWT access token (standard OAuth2 flow)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.auth_server_url}/oauth2/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": settings.registry_redirect_uri,
                        "client_id": settings.registry_app_name,
                        "client_secret": settings.registry_client_secret,
                        "code_verifier": code_verifier,
                    },
                    timeout=10.0,
                )

                if response.status_code != 200:
                    logger.error(f"Failed to exchange code for token: {response.status_code} - {response.text}")
                    return RedirectResponse(
                        url=f"{settings.registry_client_url}/login?error=oauth2_token_exchange_failed", status_code=302
                    )

                token_response = response.json()
                access_token = token_response.get("access_token")

                if not access_token:
                    logger.error("No access_token returned from auth server")
                    return RedirectResponse(
                        url=f"{settings.registry_client_url}/login?error=oauth2_invalid_response", status_code=302
                    )

                user_claims = decode_jwt(access_token, settings.jwt_public_key, settings.jwt_issuer)

                logger.info(f"OAuth2 callback exchanged code for JWT token: {user_claims.get('sub')}")

        except httpx.TimeoutException:
            logger.error("Timeout exchanging authorization code with auth server")
            return RedirectResponse(url=f"{settings.registry_client_url}/login?error=oauth2_timeout", status_code=302)
        except Exception:
            logger.exception("Failed to exchange authorization code for token")

            return RedirectResponse(
                url=f"{settings.registry_client_url}/login?error=oauth2_exchange_error", status_code=302
            )

        if not user_claims.get("user_id"):
            logger.warning(f"User {user_claims.get('sub')} has no user_id - not found in MongoDB. Creating new user.")
            user_obj = await user_service.create_user(user_claims)
        else:
            user_obj = await user_service.get_user_by_user_id(user_claims.get("user_id"))

        if not user_obj:
            logger.error(f"Failed to find or create user for claims: {user_claims}")
            return RedirectResponse(
                url=f"{settings.registry_client_url}/login?error=User+not+found+in+registry", status_code=302
            )

        # Merge OAuth claims with user object data
        # OAuth claims take precedence except for email and role which come from database
        user_info = {
            "user_id": str(user_obj.id),
            "username": user_obj.username,
            "email": user_obj.email or user_claims.get("email", ""),
            "groups": filter_known_groups(user_claims.get("groups", []), settings.scopes_file_config),
            "scopes": user_claims.get("scope", []),
            "role": user_obj.role,
            "auth_method": "oauth2",
            "provider": user_claims.get("auth_provider", "unknown"),
            "idp_id": user_claims.get("idp_id"),
            "iat": user_claims.get("iat"),
            "exp": user_claims.get("exp"),
        }

        # Generate JWT access and refresh tokens, honoring OAuth token timing
        access_token, refresh_token = generate_token_pair(user_info=user_info)

        resp = RedirectResponse(url=settings.registry_client_url.rstrip("/"), status_code=302)

        # Delete ephemeral cookie that holds PKCE code_verifier.
        resp.delete_cookie("registry_oauth2_code_verifier")

        # Determine cookie security settings
        cookie_secure = settings.session_cookie_secure and is_https

        # Set access token cookie (1 day)
        resp.set_cookie(
            key=settings.session_cookie_name,  # jarvis_registry_session
            value=access_token,
            max_age=ACCESS_TOKEN_COOKIE_MAX_AGE_SECONDS,  # 1 day in seconds
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
            path="/",
        )
        _set_csrf_cookie(resp, access_token, cookie_secure)

        # Set refresh token cookie (48 hours sliding)
        resp.set_cookie(
            key=settings.refresh_cookie_name,
            value=refresh_token,
            max_age=REFRESH_TOKEN_EXPIRES_SECONDS,
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
            path="/",
        )

        # Clean up temporary cookies
        resp.delete_cookie("oauth2_temp_session")

        logger.info(f"OAuth2 login successful for user {user_obj.username}, JWT tokens set in httpOnly cookies")
        return resp

    except Exception as e:
        logger.error(f"Error in OAuth2 callback: {e}")
        return RedirectResponse(
            url=f"{settings.registry_client_url}/login?error=oauth2_callback_error", status_code=302
        )


@router.post("/redirect/logout")
async def logout_post():
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _delete_auth_cookies(response)

    return response


@router.post("/redirect/refresh")
async def refresh_token(
    request: Request,
    refresh: Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)] = None,
    is_https: bool = Depends(check_if_https),
):
    """
    Refresh access token using refresh token from cookie.

    This endpoint is called by the frontend when it detects a 401 error.
    It validates the refresh token and generates a new access token if valid.
    """

    try:
        if not refresh:
            logger.debug("No refresh token in cookie")
            response = JSONResponse(status_code=401, content={"detail": "No refresh token available"})
            # Clear cookies when no refresh token
            _delete_auth_cookies(response)
            return response

        # Verify refresh token
        refresh_claims = verify_refresh_token(refresh)
        if not refresh_claims:
            logger.debug("Refresh token invalid or expired")
            response = JSONResponse(status_code=401, content={"detail": "Invalid or expired refresh token"})
            # Clear cookies when refresh token is invalid or expired
            _delete_auth_cookies(response)
            return response

        # Enforce 14-day absolute session cap regardless of refresh-token reissue activity
        now = int(datetime.now(UTC).timestamp())
        session_started_at = _parse_session_started_at(refresh_claims.get("session_started_at"), now)
        if session_started_at is None:
            logger.warning("Refresh token has invalid session_started_at claim")
            response = JSONResponse(status_code=401, content={"detail": "Invalid refresh token session"})
            _delete_auth_cookies(response)
            return response
        if now - session_started_at > ABSOLUTE_SESSION_EXPIRES_SECONDS:
            logger.info("Absolute session cap exceeded for refresh token; forcing re-login")
            response = JSONResponse(status_code=401, content={"detail": "Session expired, please log in again"})
            _delete_auth_cookies(response)
            return response

        # Extract user info from refresh token claims
        user_id = refresh_claims.get("user_id")
        username = refresh_claims.get("sub")
        auth_method = refresh_claims.get("auth_method")
        provider = refresh_claims.get("provider")

        # Extract groups and scopes from refresh token
        groups = refresh_claims.get("groups", [])
        scope_string = refresh_claims.get("scope", "")
        scopes = scope_string.split() if scope_string else []

        # If no scopes but has groups, map groups to scopes
        if not scopes and groups:
            scopes = map_groups_to_scopes(groups, settings.scopes_file_config)
            logger.info(f"Mapped refresh token groups {groups} to scopes: {scopes}")

        role = refresh_claims.get("role", "user")
        email = refresh_claims.get("email", f"{username}@local")

        logger.info(f"Refresh token valid for user {username} ({auth_method}), generating new access token")
        logger.debug(f"User groups from refresh token: {groups}, scopes: {scopes}")

        # Validate that we have the required information
        if not scopes:
            logger.warning(f"Refresh token for user {username} has no scopes (groups: {groups}), cannot refresh")
            response = JSONResponse(
                status_code=401, content={"detail": "Refresh token missing required scopes information"}
            )
            # Clear cookies when refresh token is missing required information
            _delete_auth_cookies(response)
            return response

        # Generate new access and refresh tokens using information from refresh token.
        # Refresh tokens are stateless JWTs, so the previous token remains valid until its exp.
        try:
            new_access_token = generate_access_token(
                user_id=user_id,
                username=username,
                email=email,
                groups=groups,
                scopes=scopes,
                role=role,
                auth_method=auth_method,
                provider=provider,
            )

            new_refresh_token = generate_refresh_token(
                user_id=user_id,
                username=username,
                auth_method=auth_method,
                provider=provider,
                groups=groups,
                scopes=scopes,
                role=role,
                email=email,
                session_started_at=session_started_at,
            )

            # Determine cookie security settings
            cookie_secure = settings.session_cookie_secure and is_https

            # Create response with new tokens
            response = JSONResponse(status_code=200, content={"detail": "Token refreshed successfully"})

            # Update access token cookie (1 day)
            response.set_cookie(
                key=settings.session_cookie_name,  # jarvis_registry_session
                value=new_access_token,
                max_age=ACCESS_TOKEN_COOKIE_MAX_AGE_SECONDS,  # 1 day in seconds
                httponly=True,
                samesite="lax",
                secure=cookie_secure,
                path="/",
            )
            _set_csrf_cookie(response, new_access_token, cookie_secure)

            # Reissue refresh token cookie (48 hours sliding)
            response.set_cookie(
                key=settings.refresh_cookie_name,
                value=new_refresh_token,
                max_age=REFRESH_TOKEN_EXPIRES_SECONDS,
                httponly=True,
                samesite="lax",
                secure=cookie_secure,
                path="/",
            )

            logger.info(f"Successfully refreshed tokens for user {username}")
            return response

        except Exception as e:
            logger.error(f"Error generating new tokens during refresh: {e}")
            return JSONResponse(status_code=500, content={"detail": "Failed to complete token refresh"})

    except Exception as e:
        logger.error(f"Error during token refresh: {e}")
        return JSONResponse(status_code=500, content={"detail": "Token refresh failed"})
