"""Unit tests for OAuth token grant dispatch and request validation."""

import logging
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from tests.support.oauth_state_store import InMemoryOAuthStateStore

from auth_server.models.token_grants import (
    AuthorizationCodeTokenRequest,
    DeviceCodeTokenRequest,
    OAuthTokenRequest,
    RefreshTokenRequest,
)
from auth_server.services.token_grant_service import TokenGrantService, validate_token_grant_request
from registry_pkgs.core.client_categories import (
    AUTHORIZATION_CODE_GRANT_TYPE,
    DEVICE_CODE_GRANT_TYPE,
    REFRESH_TOKEN_GRANT_TYPE,
)
from registry_pkgs.core.jwt_tokens import MintedManagedAgentToken


@pytest.fixture
def service() -> TokenGrantService:
    return TokenGrantService(
        store=InMemoryOAuthStateStore(),
        consent_store=Mock(),
    )


@pytest.mark.parametrize(
    "params, expected_description",
    [
        ({"client_id": "mcp-client-test"}, "grant_type is required"),
        ({"grant_type": AUTHORIZATION_CODE_GRANT_TYPE}, "client_id is required"),
    ],
)
def test_validate_request_rejects_missing_required_parameters(
    params: dict[str, str],
    expected_description: str,
) -> None:
    response = validate_token_grant_request(params)

    assert not isinstance(response, OAuthTokenRequest)
    assert response.status_code == 400
    assert expected_description.encode() in response.body


async def test_exchange_rejects_unsupported_grant(
    service: TokenGrantService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="auth_server.services.token_grant_service")

    response = await service.exchange(OAuthTokenRequest(grant_type="password", client_id="mcp-client-test"))

    assert response.status_code == 400
    assert b"unsupported_grant_type" in response.body
    assert "client_id: mcp-client-test" in caplog.text


@pytest.mark.parametrize(
    "grant_request, method_name",
    [
        (
            AuthorizationCodeTokenRequest(
                grant_type=AUTHORIZATION_CODE_GRANT_TYPE,
                client_id="mcp-client-test",
                code="code",
                redirect_uri="https://example.com/callback",
            ),
            "_exchange_authorization_code",
        ),
        (
            DeviceCodeTokenRequest(
                grant_type=DEVICE_CODE_GRANT_TYPE,
                client_id="mcp-client-test",
                device_code="device-code",
            ),
            "_exchange_device_code",
        ),
        (
            RefreshTokenRequest(
                grant_type=REFRESH_TOKEN_GRANT_TYPE,
                client_id="mcp-client-test",
                refresh_token="refresh-token",
            ),
            "_exchange_refresh_token",
        ),
    ],
)
async def test_exchange_dispatches_supported_grant(
    service: TokenGrantService,
    grant_request: AuthorizationCodeTokenRequest | DeviceCodeTokenRequest | RefreshTokenRequest,
    method_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="auth_server.services.token_grant_service")
    expected = Mock()
    with patch.object(service, method_name, new=AsyncMock(return_value=expected)) as grant_method:
        response = await service.exchange(grant_request)

    assert response is expected
    grant_method.assert_awaited_once_with(grant_request)
    assert "client_id: mcp-client-test" in caplog.text


def test_validate_request_rejects_non_string_fields() -> None:
    response = validate_token_grant_request(
        {
            "grant_type": AUTHORIZATION_CODE_GRANT_TYPE,
            "client_id": ["mcp-client-test"],
            "code": "code",
            "redirect_uri": "https://example.com/callback",
        }
    )

    assert not isinstance(response, OAuthTokenRequest)
    assert response.status_code == 400
    assert b"invalid_request" in response.body


async def test_refresh_exchange_returns_invalid_grant_when_rotation_loses_race() -> None:
    store = InMemoryOAuthStateStore()
    store.save_client(
        "mcp-client-race",
        {"grant_types": [REFRESH_TOKEN_GRANT_TYPE], "token_endpoint_auth_method": "none"},
    )
    store.save_refresh_token(
        "old-refresh-token",
        {
            "client_id": "mcp-client-race",
            "user_info": {"username": "race-user", "groups": [], "user_id": "user-123"},
            "scope": "mcp-proxy-ops",
        },
    )
    service = TokenGrantService(
        store=store,
        consent_store=Mock(),
    )
    grant_request = RefreshTokenRequest(
        grant_type=REFRESH_TOKEN_GRANT_TYPE,
        client_id="mcp-client-race",
        refresh_token="old-refresh-token",
    )

    with (
        patch.object(store, "rotate_refresh_token", return_value=None) as rotate,
        patch(
            "auth_server.services.token_grant_service.mint_managed_agent_token_with_scope",
            return_value=MintedManagedAgentToken("unused-token", "mcp-proxy-ops"),
        ) as mint,
    ):
        response = await service.exchange(grant_request)

    assert response.status_code == 400
    assert b"refresh token already used" in response.body
    mint.assert_called_once()
    rotate.assert_called_once()


async def test_refresh_exchange_propagates_unexpected_store_failure() -> None:
    store = Mock()
    store.get_refresh_token.side_effect = RuntimeError("store unavailable")
    service = TokenGrantService(store=store, consent_store=Mock())
    grant_request = RefreshTokenRequest(
        grant_type=REFRESH_TOKEN_GRANT_TYPE,
        client_id="mcp-client-test",
        refresh_token="refresh-token",
    )

    with pytest.raises(RuntimeError, match="store unavailable"):
        await service.exchange(grant_request)


def _prepare_grant(kind: str, user_info: dict) -> tuple[TokenGrantService, OAuthTokenRequest]:
    store = InMemoryOAuthStateStore()
    client_id = "mcp-client-identity"
    store.save_client(client_id, {"grant_types": [kind], "token_endpoint_auth_method": "none"})
    context = {"client_id": client_id, "expires_at": int(time.time()) + 600}
    if kind == AUTHORIZATION_CODE_GRANT_TYPE:
        store.save_authcode(
            "code",
            {
                **context,
                "user_info": user_info,
                "redirect_uri": "https://client.example/callback",
                "resolved_scope": ["mcp-proxy-ops"],
            },
        )
        request = AuthorizationCodeTokenRequest(
            grant_type=kind,
            client_id=client_id,
            code="code",
            redirect_uri="https://client.example/callback",
        )
    elif kind == DEVICE_CODE_GRANT_TYPE:
        store.save_device_code(
            "device",
            {
                **context,
                "mapped_user": user_info,
                "resolved_scope": ["mcp-proxy-ops"],
                "status": "approved",
                "user_code": "USER-CODE",
            },
            600,
        )
        request = DeviceCodeTokenRequest(grant_type=kind, client_id=client_id, device_code="device")
    else:
        store.save_refresh_token("refresh", {**context, "user_info": user_info, "scope": "mcp-proxy-ops"})
        request = RefreshTokenRequest(grant_type=kind, client_id=client_id, refresh_token="refresh")
    return TokenGrantService(store, Mock(has_client_consent=Mock(return_value=True))), request


@pytest.mark.parametrize("kind", [AUTHORIZATION_CODE_GRANT_TYPE, DEVICE_CODE_GRANT_TYPE, REFRESH_TOKEN_GRANT_TYPE])
@pytest.mark.parametrize("user_id", [None, "", "   ", 123])
async def test_grants_reject_missing_or_invalid_stored_user_id(kind: str, user_id: object) -> None:
    user_info = {"username": "same-name", "email": "person@example.com", "groups": []}
    if user_id is not None:
        user_info["user_id"] = user_id
    service, request = _prepare_grant(kind, user_info)
    with patch.object(service, "_mint_response") as mint:
        response = await service.exchange(request)
    assert response.status_code == 400
    assert b'"error":"invalid_grant"' in response.body
    assert b"Please sign in again." in response.body
    mint.assert_not_called()
    assert set(service._store.refresh_tokens_storage) == ({"refresh"} if kind == REFRESH_TOKEN_GRANT_TYPE else set())


@pytest.mark.parametrize("kind", [AUTHORIZATION_CODE_GRANT_TYPE, DEVICE_CODE_GRANT_TYPE, REFRESH_TOKEN_GRANT_TYPE])
async def test_grants_preserve_stored_identity_in_access_and_refresh_tokens(kind: str) -> None:
    user_info = {"user_id": "user-123", "username": "display-name", "groups": [], "provider": "entra"}
    service, request = _prepare_grant(kind, user_info)
    with patch(
        "auth_server.services.token_grant_service.mint_managed_agent_token_with_scope",
        return_value=MintedManagedAgentToken("access-token", "mcp-proxy-ops"),
    ) as mint:
        response = await service.exchange(request)
    assert response.access_token == "access-token"
    assert mint.call_args.kwargs["extra_claims"]["user_id"] == "user-123"
    assert mint.call_args.kwargs["subject"] == "display-name"  # Subject changes separately in AS-1900.
    assert service._store.get_refresh_token(response.refresh_token)["user_info"] == user_info


@pytest.mark.parametrize("kind", [AUTHORIZATION_CODE_GRANT_TYPE, DEVICE_CODE_GRANT_TYPE])
async def test_grants_validate_identity_from_consumed_context(kind: str) -> None:
    service, request = _prepare_grant(kind, {"user_id": "user-123", "username": "user"})
    if kind == AUTHORIZATION_CODE_GRANT_TYPE:
        consumed = {**service._store.get_authcode("code"), "user_info": {"username": "user"}}
        consume_method = "consume_authcode"
    else:
        consumed = {**service._store.get_device_code("device"), "mapped_user": {"username": "user"}}
        consume_method = "consume_device_code"
    with (
        patch.object(service._store, consume_method, return_value=consumed),
        patch.object(service, "_mint_response") as mint,
    ):
        response = await service.exchange(request)
    assert response.status_code == 400
    assert b"Please sign in again." in response.body
    mint.assert_not_called()


async def test_refresh_without_consent_does_not_mint_or_rotate() -> None:
    service, request = _prepare_grant(REFRESH_TOKEN_GRANT_TYPE, {"user_id": "user-123", "username": "user"})
    service._consent_store.has_client_consent.return_value = False
    with patch.object(service, "_mint_response") as mint:
        response = await service.exchange(request)
    assert response.status_code == 400
    assert b"User consent is required" in response.body
    service._consent_store.has_client_consent.assert_called_once_with("user-123", request.client_id)
    mint.assert_not_called()
    assert service._store.get_refresh_token("refresh") is not None


@pytest.mark.parametrize("kind", [AUTHORIZATION_CODE_GRANT_TYPE, DEVICE_CODE_GRANT_TYPE, REFRESH_TOKEN_GRANT_TYPE])
async def test_grants_reject_wrong_client_without_minting(kind: str) -> None:
    service, request = _prepare_grant(kind, {"user_id": "user-123", "username": "user"})
    request.client_id = "other-client"
    with patch.object(service, "_mint_response") as mint:
        response = await service.exchange(request)
    assert response.status_code == 400
    assert b"client_id mismatch" in response.body
    mint.assert_not_called()


def _mint_kwargs(service: TokenGrantService, user_info: dict) -> dict:
    with patch(
        "auth_server.services.token_grant_service.mint_managed_agent_token_with_scope",
        return_value=MintedManagedAgentToken("token", "scope"),
    ) as mint:
        service._mint_response(
            client_id="mcp-client",
            user_info=user_info,
            user_id="user-1",
            requested_scopes="scope",
            issued_at=int(time.time()),
            refresh_token="refresh",
            include_identity_claims=False,
        )
    _, kwargs = mint.call_args
    return kwargs["extra_claims"]


def test_mint_response_uses_per_login_provider(service: TokenGrantService) -> None:
    claims = _mint_kwargs(service, {"username": "u", "groups": [], "provider": "google"})
    assert claims["auth_provider"] == "google"


def test_mint_response_falls_back_to_settings_provider(service: TokenGrantService) -> None:
    from auth_server.services.token_grant_service import settings

    claims = _mint_kwargs(service, {"username": "u", "groups": []})
    assert claims["auth_provider"] == settings.auth_provider
