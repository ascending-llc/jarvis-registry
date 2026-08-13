"""OAuth token grant orchestration for authorization code, device code, and refresh grants."""

import logging
import secrets
import time
from typing import Any

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from registry_pkgs.core.client_categories import (
    AUTHORIZATION_CODE_GRANT_TYPE,
    REFRESH_TOKEN_GRANT_TYPE,
)
from registry_pkgs.core.consent_store import ConsentStore
from registry_pkgs.core.downstream_oauth import DEVICE_CODE_GRANT_TYPE, oauth_error_payload
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token_with_scope
from registry_pkgs.core.oauth_state_store import REFRESH_TOKEN_TTL_SECONDS, OAuthStateStoreProtocol
from registry_pkgs.core.scopes import map_groups_to_scopes

from ..core.config import settings
from ..models.device_flow import DeviceTokenResponse
from ..models.token_grants import (
    AuthorizationCodeTokenRequest,
    DeviceCodeTokenRequest,
    OAuthTokenRequest,
    RefreshTokenRequest,
    TokenGrantRequest,
)
from .oauth_client_policy import (
    is_registry_client,
    resolve_authorized_client_metadata,
)
from .user_service import UserService

logger = logging.getLogger(__name__)

ACCESS_USE = "access"
BEARER_TYPE = "Bearer"


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=oauth_error_payload(error, description))


def _required_parameter_error(params: dict[str, Any], field_names: tuple[str, ...]) -> JSONResponse | None:
    missing = [field_name for field_name in field_names if not params.get(field_name)]
    if not missing:
        return None
    if len(missing) == 1:
        return _oauth_error("invalid_request", f"{missing[0]} is required")
    return _oauth_error("invalid_request", f"{' and '.join(missing)} are required")


def validate_token_grant_request(params: dict[str, Any]) -> TokenGrantRequest | JSONResponse:
    """Validate raw JSON/form fields and preserve OAuth-compatible error responses."""
    common_error = _required_parameter_error(params, ("grant_type",)) or _required_parameter_error(
        params, ("client_id",)
    )
    if common_error is not None:
        return common_error

    grant_type = params["grant_type"]
    model: type[OAuthTokenRequest]
    required_fields: tuple[str, ...] = ()
    if grant_type == AUTHORIZATION_CODE_GRANT_TYPE:
        model = AuthorizationCodeTokenRequest
        required_fields = ("code", "redirect_uri")
    elif grant_type == DEVICE_CODE_GRANT_TYPE:
        model = DeviceCodeTokenRequest
        required_fields = ("device_code",)
    elif grant_type == REFRESH_TOKEN_GRANT_TYPE:
        model = RefreshTokenRequest
        required_fields = ("refresh_token",)
    else:
        model = OAuthTokenRequest

    required_error = _required_parameter_error(params, required_fields)
    if required_error is not None:
        return required_error
    try:
        return model.model_validate(params)
    except ValidationError:
        return _oauth_error("invalid_request", "token request fields must be strings")


def _build_refresh_data(
    client_id: str,
    user_info: dict[str, Any],
    scope: str,
    issued_at: int,
) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "user_info": user_info,
        "scope": scope,
        "expires_at": issued_at + REFRESH_TOKEN_TTL_SECONDS,
    }


class TokenGrantService:
    """Validate and execute the OAuth grants exposed by the shared token endpoint."""

    def __init__(
        self,
        user_service: UserService,
        store: OAuthStateStoreProtocol,
        consent_store: ConsentStore,
    ) -> None:
        self._user_service = user_service
        self._store = store
        self._consent_store = consent_store
        self._token_config = settings.jwt_token_config

    async def exchange(self, request: TokenGrantRequest) -> DeviceTokenResponse | JSONResponse:
        logger.info("TOKEN ENDPOINT CALLED")
        logger.info("grant_type: %s", request.grant_type)
        logger.info("client_id: %s", request.client_id)

        if isinstance(request, AuthorizationCodeTokenRequest):
            return await self._exchange_authorization_code(request)
        if isinstance(request, DeviceCodeTokenRequest):
            return await self._exchange_device_code(request)
        if isinstance(request, RefreshTokenRequest):
            return await self._exchange_refresh_token(request)
        return _oauth_error("unsupported_grant_type", f"grant_type '{request.grant_type}' is not supported")

    def _validate_client(self, client_id: str, client_secret: str | None, grant_type: str) -> JSONResponse | None:
        authorization = resolve_authorized_client_metadata(
            client_id,
            client_secret,
            grant_type,
            self._store,
            self._token_config,
        )
        if not authorization.credentials_valid:
            return _oauth_error("invalid_client", "invalid client credentials")
        if not authorization.grant_authorized:
            return _oauth_error("unauthorized_client", f"client is not authorized for {grant_type}")
        return None

    def _mint_response(
        self,
        *,
        client_id: str,
        user_info: dict[str, Any],
        user_id: str | None,
        requested_scopes: str | list[str],
        issued_at: int,
        refresh_token: str,
        include_identity_claims: bool,
    ) -> DeviceTokenResponse:
        extra_claims = {
            "user_id": user_id,
            "groups": user_info.get("groups", []),
            "token_use": ACCESS_USE,
            "auth_provider": settings.auth_provider,
        }
        if include_identity_claims:
            extra_claims.update({"name": user_info.get("name"), "idp_id": user_info.get("idp_id")})

        minted = mint_managed_agent_token_with_scope(
            self._token_config,
            subject=user_info["username"],
            client_id=client_id,
            requested_scopes=requested_scopes,
            expires_in_seconds=settings.oauth_access_token_expiry_seconds,
            iat=issued_at,
            extra_claims=extra_claims,
        )
        return DeviceTokenResponse(
            access_token=minted.token,
            token_type=BEARER_TYPE,
            expires_in=settings.oauth_access_token_expiry_seconds,
            scope=minted.scope,
            refresh_token=refresh_token,
        )

    async def _exchange_authorization_code(
        self,
        request: AuthorizationCodeTokenRequest,
    ) -> DeviceTokenResponse | JSONResponse:
        auth_code_data = self._store.get_authcode(request.code)
        if not auth_code_data:
            return _oauth_error("invalid_grant", "authorization code not found or expired")

        user_id = await self._user_service.resolve_user_id(auth_code_data["user_info"])
        logger.info("user_id: %s", user_id)

        if auth_code_data["client_id"] != request.client_id:
            return _oauth_error("invalid_client", "client_id mismatch")

        client_error = self._validate_client(
            request.client_id,
            request.client_secret,
            AUTHORIZATION_CODE_GRANT_TYPE,
        )
        if client_error is not None:
            return client_error
        if auth_code_data["redirect_uri"] != request.redirect_uri:
            return _oauth_error("invalid_grant", "redirect_uri mismatch")

        issued_at = int(time.time())
        if issued_at > auth_code_data["expires_at"]:
            return _oauth_error("invalid_grant", "authorization code expired")

        code_challenge = auth_code_data.get("code_challenge")
        if code_challenge:
            if not request.code_verifier:
                return _oauth_error("invalid_request", "code_verifier required for PKCE")
            if auth_code_data.get("code_challenge_method", "S256") != "S256":
                return _oauth_error("invalid_request", "code_challenge_method must be S256")
            if create_s256_code_challenge(request.code_verifier) != code_challenge:
                return _oauth_error("invalid_grant", "code_verifier validation failed")

        auth_code_data = self._store.consume_authcode(request.code)
        if auth_code_data is None:
            return _oauth_error("invalid_grant", "authorization code already used")

        user_info = auth_code_data["user_info"]
        resolved_scopes = auth_code_data.get("resolved_scope")
        if resolved_scopes is None:
            logger.info("No resolved_scope in auth code, computing from groups (backward compatibility)")
            user_groups = user_info.get("groups", [])
            resolved_scopes = (
                map_groups_to_scopes(user_groups, settings.scopes_file_config)
                if user_groups
                else user_info.get("scopes", [])
            )

        refresh_token = secrets.token_urlsafe(32)
        response = self._mint_response(
            client_id=request.client_id,
            user_info=user_info,
            user_id=user_id,
            requested_scopes=resolved_scopes,
            issued_at=issued_at,
            refresh_token=refresh_token,
            include_identity_claims=True,
        )
        self._store.save_refresh_token(
            refresh_token,
            _build_refresh_data(request.client_id, user_info, response.scope, issued_at),
        )
        return response

    async def _exchange_device_code(
        self,
        request: DeviceCodeTokenRequest,
    ) -> DeviceTokenResponse | JSONResponse:
        device_data = self._store.get_device_code(request.device_code)
        if not device_data:
            return _oauth_error("invalid_grant", "device_code not found")
        if device_data["client_id"] != request.client_id:
            return _oauth_error("invalid_client", "client_id mismatch")

        client_error = self._validate_client(request.client_id, request.client_secret, DEVICE_CODE_GRANT_TYPE)
        if client_error is not None:
            return client_error

        issued_at = int(time.time())
        if issued_at > device_data["expires_at"]:
            return _oauth_error("expired_token", "device_code has expired")
        if device_data["status"] == "pending":
            return _oauth_error("authorization_pending", "user has not yet authorized this request")
        if device_data["status"] == "denied":
            return _oauth_error("access_denied", "user denied authorization")
        if device_data["status"] == "scope_denied":
            return _oauth_error(
                "invalid_scope",
                device_data.get("error_description") or "Requested scopes are not available for this user",
            )
        if device_data["status"] != "approved":
            return _oauth_error(
                "server_error",
                device_data.get("error_description") or "unexpected server state",
                500,
            )

        device_data = self._store.consume_device_code(request.device_code)
        if device_data is None:
            return _oauth_error("invalid_grant", "device_code already used")

        user_info = device_data["mapped_user"]
        resolved_scopes = device_data["resolved_scope"]
        if not isinstance(user_info, dict) or resolved_scopes is None:
            return _oauth_error("server_error", "approved device code is missing user context", 500)

        user_id = await self._user_service.resolve_user_id(user_info)
        refresh_token = secrets.token_urlsafe(32)
        response = self._mint_response(
            client_id=request.client_id,
            user_info=user_info,
            user_id=user_id,
            requested_scopes=resolved_scopes,
            issued_at=issued_at,
            refresh_token=refresh_token,
            include_identity_claims=True,
        )
        self._store.save_refresh_token(
            refresh_token,
            _build_refresh_data(request.client_id, user_info, response.scope, issued_at),
        )
        self._store.delete_user_code(device_data["user_code"])
        return response

    async def _exchange_refresh_token(
        self,
        request: RefreshTokenRequest,
    ) -> DeviceTokenResponse | JSONResponse:
        refresh_data = self._store.get_refresh_token(request.refresh_token)
        if not refresh_data:
            return _oauth_error("invalid_grant", "refresh token invalid or expired")

        user_info = refresh_data["user_info"]
        stored_user_id = user_info.get("user_id")
        user_id = str(stored_user_id) if stored_user_id else await self._user_service.resolve_user_id(user_info)
        logger.info("user_id: %s", user_id)

        if refresh_data.get("client_id") != request.client_id:
            return _oauth_error("invalid_client", "client_id mismatch")

        client_error = self._validate_client(request.client_id, request.client_secret, REFRESH_TOKEN_GRANT_TYPE)
        if client_error is not None:
            return client_error

        if (
            user_id
            and not is_registry_client(request.client_id)
            and not self._consent_store.has_client_consent(user_id, request.client_id)
        ):
            return _oauth_error("invalid_grant", "User consent is required. Restart the authorization flow.")

        issued_at = int(time.time())
        requested_scopes = (refresh_data.get("scope") or "").split()
        new_refresh_token = secrets.token_urlsafe(32)
        response = self._mint_response(
            client_id=request.client_id,
            user_info=user_info,
            user_id=user_id,
            requested_scopes=requested_scopes,
            issued_at=issued_at,
            refresh_token=new_refresh_token,
            include_identity_claims=False,
        )
        rotated = self._store.rotate_refresh_token(
            old_token=request.refresh_token,
            new_token=new_refresh_token,
            new_data=_build_refresh_data(request.client_id, user_info, response.scope, issued_at),
        )
        if rotated is None:
            return _oauth_error("invalid_grant", "refresh token already used")

        logger.info("Rotated refresh token for user: %s", user_info["username"])
        return response
