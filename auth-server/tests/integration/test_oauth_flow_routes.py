"""
Integration tests for OAuth 2.0 Device Flow and Dynamic Client Registration.

Tests:
- RFC 7591 (OAuth 2.0 Dynamic Client Registration)
- RFC 8628 (OAuth 2.0 Device Authorization Grant)

Note: All OAuth endpoints are served under /auth prefix when AUTH_SERVER_API_PREFIX=/auth.
"""

import time
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from auth_server.deps import get_auth_provider, get_oauth2_config, get_user_service
from auth_server.routes.oauth_flow import DEVICE_CODE_GRANT_TYPE, generate_user_code
from tests.conftest import test_consent_store, test_pending_consent_store
from tests.support.oauth_state_store import (
    device_codes_storage,
    refresh_tokens_storage,
    registered_clients,
    test_oauth_state_store,
    user_codes_storage,
)

API_PREFIX = "/auth"

OAUTH2_CONFIG = {
    "providers": {
        "keycloak": {
            "enabled": True,
            "client_id": "provider-client",
            "client_secret": "provider-secret",
            "response_type": "code",
            "grant_type": "authorization_code",
            "scopes": ["openid", "profile", "email"],
            "auth_url": "https://idp.example.com/authorize",
            "token_url": "https://idp.example.com/token",
            "user_info_url": "https://idp.example.com/userinfo",
            "username_claim": "preferred_username",
            "email_claim": "email",
            "name_claim": "name",
            "groups_claim": "groups",
        }
    }
}


@pytest.fixture(autouse=True)
def clear_oauth_flow_route_overrides(test_client: TestClient):
    yield
    test_client.app.dependency_overrides.pop(get_auth_provider, None)
    test_client.app.dependency_overrides.pop(get_oauth2_config, None)
    test_client.app.dependency_overrides.pop(get_user_service, None)


def _cookies_from_response(response) -> SimpleCookie:
    cookies = SimpleCookie()
    for key, value in response.headers.items():
        if key.lower() == "set-cookie":
            cookies.load(value)
    return cookies


def _configure_oauth2(test_client: TestClient) -> None:
    test_client.app.dependency_overrides[get_oauth2_config] = lambda: OAUTH2_CONFIG


def _configure_user_service(test_client: TestClient, user_id: str = "user-123") -> Mock:
    user_service = Mock()
    user_service.resolve_user_id = AsyncMock(return_value=user_id)
    test_client.app.dependency_overrides[get_user_service] = lambda: user_service
    return user_service


def _configure_auth_provider(test_client: TestClient) -> None:
    test_client.app.dependency_overrides[get_auth_provider] = lambda: Mock()


def _seed_legacy_client_without_device_grant() -> None:
    test_oauth_state_store.save_client(
        "legacy-client",
        {
            "client_id": "legacy-client",
            "client_name": "Legacy Client",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


def _start_device_flow(
    test_client: TestClient,
    *,
    client_id: str = "test-client",
    scope: str | None = "servers-read agents-read",
) -> dict[str, str | int]:
    data = {"client_id": client_id}
    if scope is not None:
        data["scope"] = scope

    response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data=data)
    assert response.status_code == 200
    return response.json()


def _approve_device_directly(device_code: str) -> None:
    device_data = dict(device_codes_storage[device_code])
    device_data["status"] = "approved"
    device_data["mapped_user"] = {
        "username": "test-user",
        "email": "test@example.com",
        "name": "Test User",
        "idp_id": "idp-123",
        "groups": ["registry-users"],
        "user_id": "user-123",
        "provider": "keycloak",
    }
    device_data["resolved_scope"] = ["servers-read", "agents-read"]
    device_codes_storage[device_code] = device_data


@pytest.mark.integration
@pytest.mark.device_flow
class TestDynamicClientRegistration:
    """Integration tests for RFC 7591 Dynamic Client Registration."""

    def test_register_client_includes_device_grant_by_default(self, test_client: TestClient, clear_device_storage):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={"redirect_uris": ["https://example.com/callback"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["client_id"].startswith("mcp-client-")
        assert data["grant_types"] == ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE]
        assert data["response_types"] == ["code"]
        assert data["token_endpoint_auth_method"] == "none"
        assert data["client_id"] in registered_clients

    def test_register_client_full_metadata_keeps_device_grant(self, test_client: TestClient, clear_device_storage):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "client_name": "Test MCP Client",
                "client_uri": "https://example.com",
                "redirect_uris": ["https://example.com/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": "registry-admin",
                "contacts": ["admin@example.com"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["client_name"] == "Test MCP Client"
        assert data["grant_types"] == ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE]
        assert data["token_endpoint_auth_method"] == "client_secret_post"
        assert data["scope"] == "registry-admin"

    @pytest.mark.parametrize(
        "bad_uri",
        [
            "http://example.com/cb",
            "https://10.0.0.1/cb",
            "https://example.com/cb#frag",
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
        ],
    )
    def test_register_with_unsafe_redirect_uri_rejected(
        self,
        test_client: TestClient,
        clear_device_storage,
        bad_uri: str,
    ):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={"client_name": "Unsafe", "redirect_uris": [bad_uri]},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_redirect_uri"


@pytest.mark.integration
@pytest.mark.device_flow
class TestDeviceFlowRoutes:
    """Integration tests for RFC 8628 Device Authorization Grant endpoints."""

    def test_generate_user_code_format(self, clear_device_storage):
        user_code = generate_user_code()

        assert len(user_code) == 9
        assert user_code[4] == "-"
        assert "O" not in user_code
        assert "0" not in user_code
        assert "I" not in user_code
        assert "1" not in user_code
        assert user_code.replace("-", "").isalnum()
        assert user_code.replace("-", "").isupper()

    def test_device_authorization_success(self, test_client: TestClient, clear_device_storage):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code",
            data={"client_id": "test-client", "scope": "servers-read agents-read"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "device_code" in data
        assert "user_code" in data
        assert "verification_uri" in data
        assert "verification_uri_complete" in data
        assert data["expires_in"] == 900
        assert data["interval"] == 5
        assert data["verification_uri"] == "http://localhost:8888/auth/oauth2/device/verify"
        assert data["user_code"] in data["verification_uri_complete"]

        stored = device_codes_storage[data["device_code"]]
        assert stored["scope"] == "servers-read agents-read"
        assert stored["mapped_user"] is None
        assert stored["resolved_scope"] is None
        assert "token" not in stored

    def test_device_authorization_rejects_client_without_device_grant(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        _seed_legacy_client_without_device_grant()

        response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "legacy-client"})

        assert response.status_code == 400
        assert response.json()["error"] == "unauthorized_client"

    def test_device_authorization_unknown_client(self, test_client: TestClient, clear_device_storage):
        response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data={"client_id": "unknown-client"})

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client"

    def test_device_verify_entry_without_user_code_renders_entry_form(self, test_client: TestClient):
        response = test_client.get(f"{API_PREFIX}/oauth2/device/verify")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Enter your device code" in response.text
        assert 'name="user_code"' in response.text

    def test_device_verify_entry_with_valid_user_code_renders_confirm_page(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        data = _start_device_flow(test_client)

        response = test_client.get(f"{API_PREFIX}/oauth2/device/verify", params={"user_code": data["user_code"]})

        assert response.status_code == 200
        assert "Does this match your device?" in response.text
        assert data["user_code"] in response.text

    def test_device_verify_entry_accepts_typed_code_without_dash(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        data = _start_device_flow(test_client)
        typed_code = str(data["user_code"]).replace("-", "").lower()

        response = test_client.get(f"{API_PREFIX}/oauth2/device/verify", params={"user_code": typed_code})

        assert response.status_code == 200
        assert data["user_code"] in response.text

    def test_device_verify_entry_with_invalid_code_renders_error(self, test_client: TestClient, clear_device_storage):
        response = test_client.get(f"{API_PREFIX}/oauth2/device/verify", params={"user_code": "BAD-CODE"})

        assert response.status_code == 400
        assert "This code is invalid or has expired" in response.text

    def test_device_verify_continue_redirects_to_provider(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        _configure_oauth2(test_client)
        data = _start_device_flow(test_client)

        response = test_client.post(
            f"{API_PREFIX}/oauth2/device/verify",
            data={"user_code": data["user_code"]},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"].startswith("https://idp.example.com/authorize?")
        assert (
            "redirect_uri=http%3A%2F%2Flocalhost%3A8888%2Fauth%2Foauth2%2Fcallback%2Fkeycloak"
            in response.headers["location"]
        )

        cookies = _cookies_from_response(response)
        assert "oauth2_temp_session" in cookies

    def test_removed_unauthenticated_device_approve_route(self, test_client: TestClient, clear_device_storage):
        response = test_client.post(f"{API_PREFIX}/oauth2/device/approve", json={"user_code": "WDJB-MJHT"})

        assert response.status_code in {404, 405}

    def test_device_token_pending(self, test_client: TestClient, clear_device_storage):
        data = _start_device_flow(test_client)

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "test-client",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "authorization_pending"

    def test_device_token_denied(self, test_client: TestClient, clear_device_storage):
        data = _start_device_flow(test_client)
        device_codes_storage[data["device_code"]]["status"] = "denied"

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "test-client",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "access_denied"

    def test_device_token_expired(self, test_client: TestClient, clear_device_storage):
        data = _start_device_flow(test_client)
        device_codes_storage[data["device_code"]]["expires_at"] = int(time.time()) - 1

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "test-client",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "expired_token"

    def test_device_token_client_mismatch(self, test_client: TestClient, clear_device_storage):
        test_oauth_state_store.save_client(
            "client-2",
            {
                "client_id": "client-2",
                "grant_types": ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        data = _start_device_flow(test_client)

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "client-2",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client"

    @patch("auth_server.routes.oauth_flow.mint_managed_agent_token", return_value="mock-access-token")
    def test_device_token_success_mints_refresh_token_and_consumes_device_code(
        self,
        mock_mint_token,
        test_client: TestClient,
        clear_device_storage,
    ):
        _configure_user_service(test_client)
        data = _start_device_flow(test_client)
        _approve_device_directly(str(data["device_code"]))

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "test-client",
            },
        )

        assert response.status_code == 200
        token_data = response.json()
        assert token_data["access_token"] == "mock-access-token"
        assert token_data["token_type"] == "Bearer"
        assert token_data["expires_in"] == 3600
        assert token_data["scope"] == "servers-read agents-read"
        assert token_data["refresh_token"] in refresh_tokens_storage
        assert data["device_code"] not in device_codes_storage
        assert data["user_code"] not in user_codes_storage

        mock_mint_token.assert_called_once()
        assert mock_mint_token.call_args.kwargs["subject"] == "test-user"
        assert mock_mint_token.call_args.kwargs["extra_claims"]["user_id"] == "user-123"

        second_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "test-client",
            },
        )
        assert second_response.status_code == 400
        assert second_response.json()["error"] == "invalid_grant"


@pytest.mark.integration
@pytest.mark.device_flow
class TestDeviceFlowCallbackAndConsent:
    """Tests for the real IdP callback and client consent gate in device flow."""

    def test_device_callback_with_prior_consent_marks_device_approved(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        _configure_oauth2(test_client)
        _configure_auth_provider(test_client)
        _configure_user_service(test_client)
        data = _start_device_flow(test_client, scope="servers-read")
        verify_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/verify",
            data={"user_code": data["user_code"]},
            follow_redirects=False,
        )
        session_cookie = verify_response.cookies.get("oauth2_temp_session")
        assert session_cookie is not None

        state_param = _extract_state_from_temp_session(session_cookie)
        with (
            patch("auth_server.routes.oauth_flow.exchange_code_for_token", new_callable=AsyncMock) as exchange_token,
            patch("auth_server.routes.oauth_flow.get_user_info", new_callable=AsyncMock) as get_user_info,
            patch("auth_server.routes.oauth_flow.map_groups_to_scopes", return_value=["servers-read", "agents-read"]),
        ):
            exchange_token.return_value = {"access_token": "provider-token"}
            get_user_info.return_value = {
                "preferred_username": "test-user",
                "email": "test@example.com",
                "name": "Test User",
                "sub": "idp-123",
                "groups": ["registry-users"],
            }
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider-code", "state": state_param},
                cookies={"oauth2_temp_session": session_cookie},
            )

        assert response.status_code == 200
        exchange_token.assert_awaited_once()
        assert exchange_token.await_args.args[3] == "http://localhost:8888/auth/oauth2/callback/keycloak"
        assert "Your device is connected" in response.text
        assert device_codes_storage[data["device_code"]]["status"] == "approved"
        assert device_codes_storage[data["device_code"]]["resolved_scope"] == ["servers-read"]

    def test_device_callback_without_prior_consent_redirects_to_consent(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        _configure_oauth2(test_client)
        _configure_auth_provider(test_client)
        _configure_user_service(test_client)
        test_consent_store.default_client_consent = False
        data = _start_device_flow(test_client, scope="servers-read")
        verify_response = test_client.post(
            f"{API_PREFIX}/oauth2/device/verify",
            data={"user_code": data["user_code"]},
            follow_redirects=False,
        )
        session_cookie = verify_response.cookies.get("oauth2_temp_session")
        assert session_cookie is not None

        state_param = _extract_state_from_temp_session(session_cookie)
        with (
            patch("auth_server.routes.oauth_flow.exchange_code_for_token", new_callable=AsyncMock) as exchange_token,
            patch("auth_server.routes.oauth_flow.get_user_info", new_callable=AsyncMock) as get_user_info,
            patch("auth_server.routes.oauth_flow.map_groups_to_scopes", return_value=["servers-read", "agents-read"]),
        ):
            exchange_token.return_value = {"access_token": "provider-token"}
            get_user_info.return_value = {
                "preferred_username": "test-user",
                "email": "test@example.com",
                "name": "Test User",
                "sub": "idp-123",
                "groups": ["registry-users"],
            }
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider-code", "state": state_param},
                cookies={"oauth2_temp_session": session_cookie},
                follow_redirects=False,
            )

        assert response.status_code == 302
        exchange_token.assert_awaited_once()
        assert exchange_token.await_args.args[3] == "http://localhost:8888/auth/oauth2/callback/keycloak"
        assert "/oauth2/consent?nonce=" in response.headers["location"]
        assert device_codes_storage[data["device_code"]]["status"] == "pending"
        assert len(test_pending_consent_store.pending) == 1

    def test_approve_device_consent_marks_device_approved(self, test_client: TestClient, clear_device_storage):
        data = _start_device_flow(test_client)
        nonce = "device-consent-nonce"
        test_pending_consent_store.save(
            nonce,
            {
                "flow_type": "device",
                "device_code": data["device_code"],
                "mapped_user": {
                    "username": "test-user",
                    "email": "test@example.com",
                    "name": "Test User",
                    "idp_id": "idp-123",
                    "groups": [],
                    "user_id": "user-123",
                },
                "resolved_scopes": ["servers-read"],
                "session_data": {"client_id": "test-client"},
            },
        )

        response = test_client.post(
            f"{API_PREFIX}/oauth2/consent/approve",
            data={"nonce": nonce},
            cookies={"oauth2_consent_nonce": nonce},
        )

        assert response.status_code == 200
        assert "Your device is connected" in response.text
        assert device_codes_storage[data["device_code"]]["status"] == "approved"
        assert ("user-123", "test-client") in test_consent_store.client_consents

    def test_deny_device_consent_marks_device_denied(self, test_client: TestClient, clear_device_storage):
        data = _start_device_flow(test_client)
        nonce = "device-deny-nonce"
        test_pending_consent_store.save(
            nonce,
            {
                "flow_type": "device",
                "device_code": data["device_code"],
                "mapped_user": {"user_id": "user-123"},
                "resolved_scopes": ["servers-read"],
                "session_data": {"client_id": "test-client"},
            },
        )

        response = test_client.post(
            f"{API_PREFIX}/oauth2/consent/deny",
            data={"nonce": nonce},
            cookies={"oauth2_consent_nonce": nonce},
        )

        assert response.status_code == 200
        assert "You denied this request" in response.text
        assert device_codes_storage[data["device_code"]]["status"] == "denied"


def _extract_state_from_temp_session(session_cookie: str) -> str:
    signer = URLSafeTimedSerializer("test-secret-key-for-testing")
    session_data = signer.loads(session_cookie)
    assert session_data["flow_type"] == "device"
    return session_data["state"]
