import base64
import json
import logging
import secrets
import urllib.parse
from typing import Annotated

import httpx
from fastapi import APIRouter, Cookie, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import URLSafeTimedSerializer

from registry_pkgs import load_scopes_config
from registry_pkgs.core.scopes import map_groups_to_scopes

from ..core.config import settings
from ..services.user_service import user_service
from ..utils.crypto_utils import generate_access_token, generate_token_pair, verify_refresh_token

logger = logging.getLogger(__name__)

router = APIRouter()

# JWT / signer configuration
SECRET_KEY = settings.secret_key
signer = URLSafeTimedSerializer(SECRET_KEY)


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
async def oauth2_login_redirect(provider: str, request: Request):
    """Redirect to auth server for OAuth2 login"""
    try:
        # Build redirect URL to auth server - use external URL for browser redirects
        registry_client_url = settings.registry_client_url
        auth_external_url = settings.auth_server_external_url
        state_data = {"nonce": secrets.token_urlsafe(24), "resource": registry_client_url}
        client_state = base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode().rstrip("=")
        auth_url = (
            f"{auth_external_url}/oauth2/login/{provider}?redirect_uri={registry_client_url}&state={client_state}"
        )
        logger.info(
            f"request.base_url: {request.base_url}, registry_url: {registry_client_url}, auth_external_url: {auth_external_url}, auth_url: {auth_url}"
        )
        logger.info(f"Redirecting to OAuth2 login for provider {provider}: {auth_url}")
        return RedirectResponse(url=auth_url, status_code=302)

    except Exception as e:
        logger.error(f"Error redirecting to OAuth2 login for {provider}: {e}")
        return RedirectResponse(url="/login?error=oauth2_redirect_failed", status_code=302)


@router.get("/redirect")
async def oauth2_callback(request: Request, code: str = None, error: str = None, details: str = None):
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
                url=f"{settings.registry_client_url}/login?error={urllib.parse.quote(error_message)}", status_code=302
            )

        if not code:
            logger.error("Missing authorization code in OAuth2 callback")
            return RedirectResponse(
                url=f"{settings.registry_client_url}/login?error=oauth2_missing_code", status_code=302
            )

        # Exchange authorization code for JWT access token (standard OAuth2 flow)
        try:
            async with httpx.AsyncClient() as client:
                auth_server_url = settings.auth_server_url.rstrip("/")
                registry_redirect_uri = f"{settings.registry_url}/redirect"

                response = await client.post(
                    f"{auth_server_url}/oauth2/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": settings.registry_app_name,
                        "redirect_uri": registry_redirect_uri,
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

                # Decode JWT to extract user information (no signature verification for internal use)
                import jwt as pyjwt

                user_claims = pyjwt.decode(access_token, options={"verify_signature": False})

                logger.info(f"OAuth2 callback exchanged code for JWT token: {user_claims.get('sub')}")

        except httpx.TimeoutException:
            logger.error("Timeout exchanging authorization code with auth server")
            return RedirectResponse(url=f"{settings.registry_client_url}/login?error=oauth2_timeout", status_code=302)
        except Exception as e:
            logger.error(f"Failed to exchange authorization code for token: {e}")
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
            "groups": user_claims.get("groups", []),
            "scopes": user_claims.get("scope", []),
            "role": user_obj.role,
            "auth_method": "oauth2",
            "provider": user_claims.get("provider", "unknown"),
            "idp_id": user_claims.get("idp_id"),
            "iat": user_claims.get("iat"),
            "exp": user_claims.get("exp"),
        }

        # Generate JWT access and refresh tokens, honoring OAuth token timing
        access_token, refresh_token = generate_token_pair(user_info=user_info)

        response = RedirectResponse(url=settings.registry_client_url.rstrip("/"), status_code=302)

        # Determine cookie security settings
        x_forwarded_proto = request.headers.get("x-forwarded-proto", "")
        is_https = x_forwarded_proto == "https" or request.url.scheme == "https"
        cookie_secure = settings.session_cookie_secure and is_https

        # Set access token cookie (1 day)
        response.set_cookie(
            key=settings.session_cookie_name,  # jarvis_registry_session
            value=access_token,
            max_age=86400,  # 1 day in seconds
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
            path="/",
        )

        # Set refresh token cookie (7 days)
        response.set_cookie(
            key=settings.refresh_cookie_name,
            value=refresh_token,
            max_age=604800,  # 7 days in seconds
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
            path="/",
        )

        # Clean up temporary cookies
        response.delete_cookie("oauth2_temp_session")

        logger.info(f"OAuth2 login successful for user {user_obj.username}, JWT tokens set in httpOnly cookies")
        return response

    except Exception as e:
        logger.error(f"Error in OAuth2 callback: {e}")
        return RedirectResponse(
            url=f"{settings.registry_client_url}/login?error=oauth2_callback_error", status_code=302
        )


async def logout_handler(
    request: Request, session: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None
):
    """Shared logout logic for GET and POST requests"""
    try:
        # Check if user was logged in via OAuth2
        provider = None
        if session:
            try:
                import jwt as pyjwt

                # Try to decode JWT to check auth method
                claims = pyjwt.decode(
                    session,
                    settings.secret_key,
                    algorithms=["HS256"],
                    options={"verify_exp": False},  # Don't verify expiration for logout
                )

                if claims.get("auth_method") == "oauth2":
                    provider = claims.get("provider")
                    logger.info(f"User was authenticated via OAuth2 provider: {provider}")

            except Exception as e:
                logger.debug(f"Could not decode JWT for logout: {e}")

        # Clear all authentication cookies
        response = RedirectResponse(url=f"{settings.registry_client_url}/login", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(settings.session_cookie_name, path="/")
        response.delete_cookie(settings.refresh_cookie_name, path="/")

        # If user was logged in via OAuth2, redirect to provider logout
        if provider:
            auth_external_url = settings.auth_server_external_url
            redirect_uri = f"{settings.registry_client_url}/logout"

            logout_url = f"{auth_external_url}/oauth2/logout/{provider}?redirect_uri={redirect_uri}"
            logger.info(f"Redirecting to {provider} logout: {logout_url}")
            response = RedirectResponse(url=logout_url, status_code=status.HTTP_303_SEE_OTHER)
            response.delete_cookie(settings.session_cookie_name, path="/")
            response.delete_cookie(settings.refresh_cookie_name, path="/")

        logger.info("User logged out, JWT cookies cleared")
        return response

    except Exception as e:
        logger.error(f"Error during logout: {e}")
        # Fallback to simple logout
        response = RedirectResponse(url=f"{settings.registry_client_url}/login", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(settings.session_cookie_name, path="/")
        response.delete_cookie(settings.refresh_cookie_name, path="/")
        return response


@router.post("/redirect/logout")
async def logout_post(
    request: Request, session: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None
):
    """Handle logout via POST request (for forms)"""
    return await logout_handler(request, session)


@router.post("/redirect/refresh")
async def refresh_token(
    request: Request,
    refresh: Annotated[str | None, Cookie(alias="jarvis_registry_refresh")] = None,
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
            response.delete_cookie(key=settings.session_cookie_name, path="/")
            response.delete_cookie(key=settings.refresh_cookie_name, path="/")
            return response

        # Verify refresh token
        refresh_claims = verify_refresh_token(refresh)
        if not refresh_claims:
            logger.debug("Refresh token invalid or expired")
            response = JSONResponse(status_code=401, content={"detail": "Invalid or expired refresh token"})
            # Clear cookies when refresh token is invalid or expired
            response.delete_cookie(key=settings.session_cookie_name, path="/")
            response.delete_cookie(key=settings.refresh_cookie_name, path="/")
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
            scopes = map_groups_to_scopes(groups, load_scopes_config())
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
            response.delete_cookie(key=settings.session_cookie_name, path="/")
            response.delete_cookie(key=settings.refresh_cookie_name, path="/")
            return response

        # Generate new access token using information from refresh token
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

            # Determine cookie security settings
            x_forwarded_proto = request.headers.get("x-forwarded-proto", "")
            is_https = x_forwarded_proto == "https" or request.url.scheme == "https"
            cookie_secure = settings.session_cookie_secure and is_https

            # Create response with new access token
            response = JSONResponse(status_code=200, content={"detail": "Token refreshed successfully"})

            # Update access token cookie (1 day)
            response.set_cookie(
                key=settings.session_cookie_name,  # jarvis_registry_session
                value=new_access_token,
                max_age=86400,  # 1 day in seconds
                httponly=True,
                samesite="lax",
                secure=cookie_secure,
                path="/",
            )

            logger.info(f"Successfully refreshed access token for user {username}")
            return response

        except Exception as e:
            logger.error(f"Error generating new access token during refresh: {e}")
            return JSONResponse(status_code=500, content={"detail": "Failed to generate new access token"})

    except Exception as e:
        logger.error(f"Error during token refresh: {e}")
        return JSONResponse(status_code=500, content={"detail": "Token refresh failed"})
