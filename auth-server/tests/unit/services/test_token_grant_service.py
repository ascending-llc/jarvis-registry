"""Unit tests for OAuth token grant dispatch and request validation."""

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
        user_service=Mock(),
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


async def test_exchange_rejects_unsupported_grant(service: TokenGrantService) -> None:
    response = await service.exchange(OAuthTokenRequest(grant_type="password", client_id="mcp-client-test"))

    assert response.status_code == 400
    assert b"unsupported_grant_type" in response.body


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
) -> None:
    expected = Mock()
    with patch.object(service, method_name, new=AsyncMock(return_value=expected)) as grant_method:
        response = await service.exchange(grant_request)

    assert response is expected
    grant_method.assert_awaited_once_with(grant_request)


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
            "user_info": {"username": "race-user", "groups": []},
            "scope": "mcp-proxy-ops",
        },
    )
    service = TokenGrantService(
        user_service=Mock(resolve_user_id=AsyncMock(return_value=None)),
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
    service = TokenGrantService(user_service=Mock(), store=store, consent_store=Mock())
    grant_request = RefreshTokenRequest(
        grant_type=REFRESH_TOKEN_GRANT_TYPE,
        client_id="mcp-client-test",
        refresh_token="refresh-token",
    )

    with pytest.raises(RuntimeError, match="store unavailable"):
        await service.exchange(grant_request)
