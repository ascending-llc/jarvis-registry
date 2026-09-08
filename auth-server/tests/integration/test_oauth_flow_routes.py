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

from auth_server.deps import get_auth_provider, get_oauth2_config, get_token_grant_service, get_user_service
from auth_server.routes.consent_templates import render_device_server_error_page
from auth_server.routes.oauth_flow import DEVICE_CODE_GRANT_TYPE, generate_user_code
from auth_server.services.token_grant_service import TokenGrantService
from registry_pkgs.core.jwt_tokens import MintedManagedAgentToken
from registry_pkgs.core.jwt_utils import decode_jwt_unverified
from tests.conftest import test_consent_store, test_pending_consent_store
from tests.support.oauth_state_store import (
    authorization_codes_storage,
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
        },
        "entra": {
            "enabled": True,
            "client_id": "entra-provider-client",
            "client_secret": "entra-provider-secret",
            "response_type": "code",
            "grant_type": "authorization_code",
            "scopes": ["openid", "profile", "email"],
            "auth_url": "https://login.microsoftonline.com/authorize",
            "token_url": "https://login.microsoftonline.com/token",
            "user_info_url": "https://graph.microsoft.com/oidc/userinfo",
            "username_claim": "preferred_username",
            "email_claim": "email",
            "name_claim": "name",
            "groups_claim": "groups",
        },
    }
}


@pytest.fixture(autouse=True)
def clear_oauth_flow_route_overrides(test_client: TestClient):
    yield
    test_client.app.dependency_overrides.pop(get_auth_provider, None)
    test_client.app.dependency_overrides.pop(get_oauth2_config, None)
    test_client.app.dependency_overrides.pop(get_user_service, None)
    test_client.app.dependency_overrides.pop(get_token_grant_service, None)


def _cookies_from_response(response) -> SimpleCookie:
    cookies = SimpleCookie()
    for key, value in response.headers.items():
        if key.lower() == "set-cookie":
            cookies.load(value)
    return cookies


def _configure_oauth2(test_client: TestClient) -> None:
    test_client.app.dependency_overrides[get_oauth2_config] = lambda: OAUTH2_CONFIG


def _configure_user_service(test_client: TestClient, user_id: str = "user-123") -> Mock:
    """Override user_service everywhere it's consulted: directly (oauth2_callback) and via
    TokenGrantService (the /oauth2/token grant branches), mirroring get_server_service-style
    overrides for composed services rather than trying to swap a sub-dependency and expect
    propagation into the container-cached TokenGrantService.
    """
    user_service = Mock()
    user_service.resolve_user_id = AsyncMock(return_value=user_id)
    test_client.app.dependency_overrides[get_user_service] = lambda: user_service
    test_client.app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
        user_service, test_oauth_state_store, test_consent_store
    )
    return user_service


def _mock_auth_provider_override() -> Mock:
    return Mock()


def _configure_auth_provider(test_client: TestClient) -> None:
    test_client.app.dependency_overrides[get_auth_provider] = _mock_auth_provider_override


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
    client_id: str = "mcp-client-test",
    scope: str | None = "mcp-proxy-ops",
) -> dict[str, str | int]:
    data = {"client_id": client_id}
    if scope is not None:
        data["scope"] = scope

    response = test_client.post(f"{API_PREFIX}/oauth2/device/code", data=data)
    assert response.status_code == 200
    return response.json()


def _approve_device_directly(device_code: str, resolved_scope: str = "mcp-proxy-ops") -> None:
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
    device_data["resolved_scope"] = [resolved_scope]
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

    def test_register_client_full_metadata_persists_declared_grant_types(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "client_name": "Test MCP Client",
                "client_uri": "https://example.com",
                "redirect_uris": ["https://example.com/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": "mcp-proxy-ops",
                "contacts": ["admin@example.com"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["client_name"] == "Test MCP Client"
        assert data["grant_types"] == ["authorization_code"]
        assert registered_clients[data["client_id"]]["grant_types"] == ["authorization_code"]
        assert data["token_endpoint_auth_method"] == "client_secret_post"
        assert data["scope"] == "mcp-proxy-ops"

    def test_register_client_rejects_unsupported_grant_types_without_persisting(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        existing_client_ids = set(registered_clients)

        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "grant_types": ["client_credentials"],
            },
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": "invalid_client_metadata",
            "error_description": "none of the requested grant_types are supported",
        }
        assert set(registered_clients) == existing_client_ids

    def test_registered_authorization_code_client_cannot_start_device_flow(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        registration = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "grant_types": ["authorization_code"],
            },
        )
        assert registration.status_code == 200
        client_id = registration.json()["client_id"]

        response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code",
            data={"client_id": client_id},
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": "unauthorized_client",
            "error_description": "client is not registered for the device_code grant type",
        }

    def test_register_client_preserves_scope_outside_category_ceiling(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={"redirect_uris": ["https://example.com/callback"], "scope": "registry-admin"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["scope"] == "registry-admin"
        assert registered_clients[data["client_id"]]["scope"] == "registry-admin"

    def test_register_client_preserves_whitespace_only_scope(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={"redirect_uris": ["https://example.com/callback"], "scope": "   "},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["scope"] == "   "
        assert registered_clients[data["client_id"]]["scope"] == "   "

    def test_register_client_substitutes_unsupported_auth_method_with_none(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        """client_secret_basic (and any other unsupported method) is downgraded to 'none'."""
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token_endpoint_auth_method"] == "none"
        assert registered_clients[data["client_id"]]["token_endpoint_auth_method"] == "none"

    def test_registration_store_failure_is_wrapped_as_500(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        with patch.object(test_oauth_state_store, "save_client", side_effect=RuntimeError("store unavailable")):
            response = test_client.post(
                f"{API_PREFIX}/oauth2/register",
                json={"redirect_uris": ["https://example.com/callback"]},
            )

        assert response.status_code == 500
        assert response.json() == {"detail": "Client registration failed"}

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
class TestA2ADynamicClientRegistration:
    """Integration tests for A2A Dynamic Client Registration endpoint."""

    def test_register_a2a_client_default(self, test_client: TestClient, clear_device_storage):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register/a2a",
            json={"redirect_uris": ["https://example.com/callback"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["client_id"].startswith("a2a-client-")
        assert data["grant_types"] == ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE]
        assert data["response_types"] == ["code"]
        assert data["token_endpoint_auth_method"] == "none"
        assert data["scope"] == "a2a-proxy-ops"
        assert data["client_name"] == "A2A Client"
        assert data["client_id"] in registered_clients

    def test_register_a2a_client_full_metadata(self, test_client: TestClient, clear_device_storage):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register/a2a",
            json={
                "client_name": "My A2A Agent",
                "client_uri": "https://example.com",
                "redirect_uris": ["https://example.com/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": "a2a-proxy-ops",
                "contacts": ["admin@example.com"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["client_id"].startswith("a2a-client-")
        assert data["client_name"] == "My A2A Agent"
        assert data["grant_types"] == ["authorization_code"]
        assert registered_clients[data["client_id"]]["grant_types"] == ["authorization_code"]
        assert data["token_endpoint_auth_method"] == "client_secret_post"
        assert data["scope"] == "a2a-proxy-ops"

    def test_register_a2a_client_substitutes_unsupported_auth_method(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register/a2a",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token_endpoint_auth_method"] == "none"
        assert registered_clients[data["client_id"]]["token_endpoint_auth_method"] == "none"

    def test_register_a2a_client_preserves_requested_scope(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register/a2a",
            json={"redirect_uris": ["https://example.com/callback"], "scope": "mcp-proxy-ops"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["scope"] == "mcp-proxy-ops"
        assert registered_clients[data["client_id"]]["scope"] == "mcp-proxy-ops"

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
    def test_register_a2a_with_unsafe_redirect_uri_rejected(
        self,
        test_client: TestClient,
        clear_device_storage,
        bad_uri: str,
    ):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register/a2a",
            json={"client_name": "Unsafe", "redirect_uris": [bad_uri]},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_redirect_uri"

    def test_mcp_register_still_produces_mcp_prefix(self, test_client: TestClient, clear_device_storage):
        """Regression: root /oauth2/register still generates mcp-client-* IDs."""
        response = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={"redirect_uris": ["https://example.com/callback"]},
        )

        assert response.status_code == 200
        assert response.json()["client_id"].startswith("mcp-client-")

    def test_a2a_discovery_registration_and_token_scope_closed_loop(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        _configure_user_service(test_client)
        discovery = test_client.get("/.well-known/oauth-authorization-server/a2a")
        assert discovery.status_code == 200

        registration_path = discovery.json()["registration_endpoint"].removeprefix("http://localhost:8888")
        redirect_uri = "https://a2a.example.com/callback"
        registration = test_client.post(registration_path, json={"redirect_uris": [redirect_uri]})
        assert registration.status_code == 200
        client_id = registration.json()["client_id"]

        authorization_codes_storage["a2a-auth-code"] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "expires_at": int(time.time()) + 600,
            "user_info": {"username": "a2a-user", "groups": ["registry-users"]},
            "resolved_scope": ["a2a-proxy-ops", "mcp-proxy-ops"],
        }
        token_response = test_client.post(
            discovery.json()["token_endpoint"].removeprefix("http://localhost:8888"),
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": "a2a-auth-code",
                "redirect_uri": redirect_uri,
            },
        )

        assert token_response.status_code == 200
        token_data = token_response.json()
        assert token_data["scope"] == "a2a-proxy-ops"
        assert decode_jwt_unverified(token_data["access_token"])["scope"] == "a2a-proxy-ops"
        assert refresh_tokens_storage[token_data["refresh_token"]]["scope"] == "a2a-proxy-ops"


@pytest.mark.integration
@pytest.mark.oauth_flow
class TestLoginRedirectErrorConsent:
    def test_client_without_authorization_code_uses_consent_detour(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        _configure_oauth2(test_client)
        redirect_uri = "http://localhost:43123/callback"
        registration = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "redirect_uris": [redirect_uri],
                "grant_types": [DEVICE_CODE_GRANT_TYPE, "refresh_token"],
            },
        )
        assert registration.status_code == 200
        client_id = registration.json()["client_id"]

        response = test_client.get(
            f"{API_PREFIX}/oauth2/login/entra",
            params={
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
                "state": "client-state",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        nonce = response.headers["location"].split("nonce=", maxsplit=1)[1]
        assert test_pending_consent_store.peek(nonce) == {
            "flow_type": "redirect_error",
            "redirect_uri": redirect_uri,
            "error": "unauthorized_client",
            "error_description": "client is not authorized for authorization_code",
            "client_state": "client-state",
        }

    def test_client_without_authorization_code_and_untrusted_redirect_returns_json_error(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        _configure_oauth2(test_client)
        redirect_uri = "https://example.com/callback"
        registration = test_client.post(
            f"{API_PREFIX}/oauth2/register",
            json={
                "redirect_uris": [redirect_uri],
                "grant_types": [DEVICE_CODE_GRANT_TYPE, "refresh_token"],
            },
        )
        assert registration.status_code == 200

        response = test_client.get(
            f"{API_PREFIX}/oauth2/login/entra",
            params={
                "client_id": registration.json()["client_id"],
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": "unauthorized_client",
            "error_description": "client is not authorized for authorization_code",
        }
        assert test_pending_consent_store.pending == {}

    @pytest.mark.parametrize(
        "redirect_uri",
        [
            "http://127.0.0.1:43123/callback",
            "http://localhost:43123/callback",
            "https://vscode.dev/redirect",
            "http://localhost:8888/api/mcp/jarvis_registry/oauth/callback",
        ],
    )
    def test_unknown_client_safe_redirect_uses_consent_detour(
        self,
        test_client: TestClient,
        clear_device_storage,
        redirect_uri: str,
    ) -> None:
        _configure_oauth2(test_client)

        response = test_client.get(
            f"{API_PREFIX}/oauth2/login/entra",
            params={
                "client_id": "unknown-client",
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
                "state": "client-state",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "/oauth2/redirect-error-consent?nonce=" in response.headers["location"]
        nonce = response.headers["location"].split("nonce=", maxsplit=1)[1]
        assert test_pending_consent_store.peek(nonce) == {
            "flow_type": "redirect_error",
            "redirect_uri": redirect_uri,
            "error": "invalid_client",
            "error_description": "Unknown client_id",
            "client_state": "client-state",
        }

    @pytest.mark.parametrize(
        "redirect_uri",
        [
            "https://vscode.dev/redirect/extra",
            "https://evil.example.com/callback",
        ],
    )
    def test_unknown_client_unsafe_redirect_keeps_json_error(
        self,
        test_client: TestClient,
        clear_device_storage,
        redirect_uri: str,
    ) -> None:
        _configure_oauth2(test_client)

        response = test_client.get(
            f"{API_PREFIX}/oauth2/login/entra",
            params={
                "client_id": "unknown-client",
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": "invalid_client",
            "error_description": "Unknown client_id",
        }
        assert test_pending_consent_store.pending == {}

    def test_known_client_unregistered_safe_redirect_uses_same_consent_detour(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        _configure_oauth2(test_client)

        response = test_client.get(
            f"{API_PREFIX}/oauth2/login/entra",
            params={
                "client_id": "mcp-client-test",
                "response_type": "code",
                "redirect_uri": "http://localhost:43123/different-path",
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        nonce = response.headers["location"].split("nonce=", maxsplit=1)[1]
        pending = test_pending_consent_store.peek(nonce)
        assert pending is not None
        assert pending["error"] == "invalid_request"
        assert pending["error_description"] == "redirect_uri is not registered for this client"

    def test_known_client_unregistered_unsafe_redirect_keeps_json_error(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        _configure_oauth2(test_client)

        response = test_client.get(
            f"{API_PREFIX}/oauth2/login/entra",
            params={
                "client_id": "mcp-client-test",
                "response_type": "code",
                "redirect_uri": "https://evil.example.com/callback",
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": "invalid_request",
            "error_description": "redirect_uri is not registered for this client",
        }
        assert test_pending_consent_store.pending == {}


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
            data={"client_id": "mcp-client-test", "scope": "mcp-proxy-ops"},
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
        assert stored["scope"] == "mcp-proxy-ops"
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

    def test_device_authorization_cli_without_seed(self, test_client: TestClient, clear_device_storage):
        """CLI client succeeds via static metadata without Redis seed."""
        response = test_client.post(
            f"{API_PREFIX}/oauth2/device/code",
            data={"client_id": "jarvis-registry-cli", "scope": "skills-read"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "device_code" in data
        assert "user_code" in data

    def test_cli_device_flow_and_refresh_keep_skills_read_scope(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        user_service = _configure_user_service(test_client)
        device_data = _start_device_flow(
            test_client,
            client_id="jarvis-registry-cli",
            scope="skills-read",
        )
        nonce = "cli-device-consent-nonce"
        test_pending_consent_store.save(
            nonce,
            {
                "flow_type": "device",
                "device_code": device_data["device_code"],
                "mapped_user": {
                    "username": "test-user",
                    "email": "test@example.com",
                    "name": "Test User",
                    "idp_id": "idp-123",
                    "groups": ["registry-users"],
                    "user_id": "user-123",
                },
                "resolved_scopes": ["skills-read"],
                "session_data": {"client_id": "jarvis-registry-cli"},
            },
        )
        test_client.cookies.set("oauth2_consent_nonce", nonce)
        approval = test_client.post(f"{API_PREFIX}/oauth2/consent/approve", data={"nonce": nonce})

        assert approval.status_code == 200
        assert device_codes_storage[device_data["device_code"]]["status"] == "approved"
        assert test_consent_store.has_client_consent("user-123", "jarvis-registry-cli") is True

        token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": device_data["device_code"],
                "client_id": "jarvis-registry-cli",
            },
        )

        assert token_response.status_code == 200
        first_token = token_response.json()
        assert first_token["scope"] == "skills-read"
        assert decode_jwt_unverified(first_token["access_token"])["scope"] == "skills-read"
        assert first_token["refresh_token"] in refresh_tokens_storage
        assert "jarvis-registry-cli" not in registered_clients

        refresh_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": first_token["refresh_token"],
                "client_id": "jarvis-registry-cli",
            },
        )

        assert refresh_response.status_code == 200
        refreshed_token = refresh_response.json()
        assert refreshed_token["scope"] == "skills-read"
        assert decode_jwt_unverified(refreshed_token["access_token"])["scope"] == "skills-read"
        assert first_token["refresh_token"] not in refresh_tokens_storage
        assert refresh_tokens_storage[refreshed_token["refresh_token"]]["scope"] == "skills-read"
        assert user_service.resolve_user_id.await_count == 1

    def test_cli_authorization_code_grant_is_rejected(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        authorization_codes_storage["cli-auth-code"] = {
            "client_id": "jarvis-registry-cli",
            "redirect_uri": "http://localhost/callback",
            "expires_at": int(time.time()) + 600,
            "user_info": {"username": "test-user", "groups": []},
            "resolved_scope": ["skills-read"],
        }

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "jarvis-registry-cli",
                "code": "cli-auth-code",
                "redirect_uri": "http://localhost/callback",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "unauthorized_client"
        assert "cli-auth-code" in authorization_codes_storage

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
                "client_id": "mcp-client-test",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "authorization_pending"

    def test_token_route_wraps_unexpected_store_failure(
        self,
        test_client: TestClient,
        clear_device_storage,
    ) -> None:
        with patch.object(test_oauth_state_store, "get_device_code", side_effect=RuntimeError("store unavailable")):
            response = test_client.post(
                f"{API_PREFIX}/oauth2/token",
                data={
                    "grant_type": DEVICE_CODE_GRANT_TYPE,
                    "device_code": "device-code",
                    "client_id": "mcp-client-test",
                },
            )

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}

    def test_device_token_denied(self, test_client: TestClient, clear_device_storage):
        data = _start_device_flow(test_client)
        device_codes_storage[data["device_code"]]["status"] = "denied"

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "mcp-client-test",
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
                "client_id": "mcp-client-test",
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

    @patch("auth_server.services.token_grant_service.mint_managed_agent_token_with_scope")
    def test_device_token_success_mints_refresh_token_and_consumes_device_code(
        self,
        mock_mint_token,
        test_client: TestClient,
        clear_device_storage,
    ):
        mock_mint_token.return_value = MintedManagedAgentToken("mock-access-token", "mcp-proxy-ops")
        _configure_user_service(test_client)
        data = _start_device_flow(test_client)
        _approve_device_directly(str(data["device_code"]))

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "mcp-client-test",
            },
        )

        assert response.status_code == 200
        token_data = response.json()
        assert token_data["access_token"] == "mock-access-token"
        assert token_data["token_type"] == "Bearer"
        assert token_data["expires_in"] == 3600
        assert token_data["scope"] == "mcp-proxy-ops"
        assert token_data["refresh_token"] in refresh_tokens_storage
        assert refresh_tokens_storage[token_data["refresh_token"]]["scope"] == token_data["scope"]
        assert data["device_code"] not in device_codes_storage
        assert data["user_code"] not in user_codes_storage

        mock_mint_token.assert_called_once()
        assert mock_mint_token.call_args.kwargs["subject"] == "test-user"
        assert mock_mint_token.call_args.kwargs["requested_scopes"] == [token_data["scope"]]
        assert mock_mint_token.call_args.kwargs["extra_claims"]["user_id"] == "user-123"

        second_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "mcp-client-test",
            },
        )
        assert second_response.status_code == 400
        assert second_response.json()["error"] == "invalid_grant"

    def test_device_token_rejects_when_atomic_consume_loses_the_race(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        """A concurrent poll can still observe status == "approved" via get_device_code, but must
        lose if another poll already won the atomic consume_device_code race."""
        _configure_user_service(test_client)
        data = _start_device_flow(test_client)
        _approve_device_directly(str(data["device_code"]))

        with patch.object(test_oauth_state_store, "consume_device_code", return_value=None) as mock_consume:
            response = test_client.post(
                f"{API_PREFIX}/oauth2/token",
                data={
                    "grant_type": DEVICE_CODE_GRANT_TYPE,
                    "device_code": data["device_code"],
                    "client_id": "mcp-client-test",
                },
            )

        mock_consume.assert_called_once_with(data["device_code"])
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"
        assert data["device_code"] in device_codes_storage

    def test_device_token_rejects_invalid_client_secret(self, test_client: TestClient, clear_device_storage):
        """A client_secret_post-registered device client must present its registered secret."""
        test_oauth_state_store.save_client(
            "mcp-client-confidential",
            {
                "client_id": "mcp-client-confidential",
                "client_secret": "correct-secret",
                "grant_types": ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        data = _start_device_flow(test_client, client_id="mcp-client-confidential")
        _approve_device_directly(str(data["device_code"]))

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "mcp-client-confidential",
                "client_secret": "wrong-secret",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client"
        assert data["device_code"] in device_codes_storage

    @patch("auth_server.services.token_grant_service.mint_managed_agent_token_with_scope")
    def test_device_token_accepts_valid_client_secret(
        self,
        mock_mint_token,
        test_client: TestClient,
        clear_device_storage,
    ):
        """A client_secret_post-registered device client mints a token when its secret matches."""
        mock_mint_token.return_value = MintedManagedAgentToken("mock-access-token", "mcp-proxy-ops")
        test_oauth_state_store.save_client(
            "mcp-client-confidential",
            {
                "client_id": "mcp-client-confidential",
                "client_secret": "correct-secret",
                "grant_types": ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        _configure_user_service(test_client)
        data = _start_device_flow(test_client, client_id="mcp-client-confidential")
        _approve_device_directly(str(data["device_code"]))

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "mcp-client-confidential",
                "client_secret": "correct-secret",
            },
        )

        assert response.status_code == 200
        assert response.json()["access_token"] == "mock-access-token"
        mock_mint_token.assert_called_once()


@pytest.mark.integration
@pytest.mark.device_flow
class TestDeviceFlowCallbackAndConsent:
    """Tests for the real IdP callback and client consent gate in device flow."""

    def test_device_callback_exception_is_terminal(
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
        with patch(
            "auth_server.routes.oauth_flow.exchange_code_for_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("provider unavailable"),
        ):
            test_client.cookies.set("oauth2_temp_session", session_cookie)
            callback_response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider-code", "state": state_param},
                follow_redirects=False,
            )

        token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "mcp-client-test",
            },
        )

        assert callback_response.status_code == 500
        assert callback_response.text == render_device_server_error_page()
        assert device_codes_storage[data["device_code"]]["status"] == "failed"
        assert device_codes_storage[data["device_code"]]["error_description"] == "Unexpected error during sign-in"
        assert token_response.status_code == 500
        assert token_response.json()["error"] == "server_error"
        assert token_response.json()["error_description"] == "Unexpected error during sign-in"

    def test_device_callback_exception_preserves_terminal_status(
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

        device_codes_storage[data["device_code"]]["status"] = "denied"
        state_param = _extract_state_from_temp_session(session_cookie)
        with patch(
            "auth_server.routes.oauth_flow.exchange_code_for_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("provider unavailable"),
        ):
            test_client.cookies.set("oauth2_temp_session", session_cookie)
            callback_response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider-code", "state": state_param},
                follow_redirects=False,
            )

        token_response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": data["device_code"],
                "client_id": "mcp-client-test",
            },
        )

        assert callback_response.status_code == 500
        assert device_codes_storage[data["device_code"]]["status"] == "denied"
        assert "error_description" not in device_codes_storage[data["device_code"]]
        assert token_response.status_code == 400
        assert token_response.json()["error"] == "access_denied"

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
            test_client.cookies.set("oauth2_temp_session", session_cookie)
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider-code", "state": state_param},
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
            test_client.cookies.set("oauth2_temp_session", session_cookie)
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/keycloak",
                params={"code": "provider-code", "state": state_param},
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
                "session_data": {"client_id": "mcp-client-test"},
            },
        )

        test_client.cookies.set("oauth2_consent_nonce", nonce)
        response = test_client.post(
            f"{API_PREFIX}/oauth2/consent/approve",
            data={"nonce": nonce},
        )

        assert response.status_code == 200
        assert "Your device is connected" in response.text
        assert device_codes_storage[data["device_code"]]["status"] == "approved"
        assert ("user-123", "mcp-client-test") in test_consent_store.client_consents

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
                "session_data": {"client_id": "mcp-client-test"},
            },
        )

        test_client.cookies.set("oauth2_consent_nonce", nonce)
        response = test_client.post(
            f"{API_PREFIX}/oauth2/consent/deny",
            data={"nonce": nonce},
        )

        assert response.status_code == 200
        assert "You denied this request" in response.text
        assert device_codes_storage[data["device_code"]]["status"] == "denied"

    def test_consent_page_shows_cli_name(self, test_client: TestClient, clear_device_storage):
        """CLI consent page shows 'Jarvis Registry CLI' via static metadata."""
        nonce = "cli-consent-nonce"
        test_pending_consent_store.save(
            nonce,
            {
                "flow_type": "device",
                "device_code": "dummy-device-code",
                "mapped_user": {"user_id": "user-123"},
                "resolved_scopes": ["skills-read"],
                "session_data": {"client_id": "jarvis-registry-cli"},
            },
        )

        test_client.cookies.set("oauth2_consent_nonce", nonce)
        response = test_client.get(f"{API_PREFIX}/oauth2/consent", params={"nonce": nonce})

        assert response.status_code == 200
        assert "Jarvis Registry CLI" in response.text
        assert "Unknown application" not in response.text
        assert "<code>skills-read</code>" in response.text
        assert "View skills and their content and files." in response.text

    def test_consent_page_shows_only_mcp_client_ceiling_scope(
        self,
        test_client: TestClient,
        clear_device_storage,
    ):
        nonce = "mcp-consent-nonce"
        test_pending_consent_store.save(
            nonce,
            {
                "flow_type": "device",
                "device_code": "dummy-device-code",
                "mapped_user": {"user_id": "user-123"},
                "resolved_scopes": ["servers-read", "mcp-proxy-ops"],
                "session_data": {"client_id": "mcp-client-test"},
            },
        )

        test_client.cookies.set("oauth2_consent_nonce", nonce)
        response = test_client.get(f"{API_PREFIX}/oauth2/consent", params={"nonce": nonce})

        assert response.status_code == 200
        assert "<code>mcp-proxy-ops</code>" in response.text
        assert "Act on your behalf to connect to and call tools on your registered MCP servers." in response.text
        assert "servers-read" not in response.text


def _extract_state_from_temp_session(session_cookie: str) -> str:
    signer = URLSafeTimedSerializer("test-secret-key-for-testing")
    session_data = signer.loads(session_cookie)
    assert session_data["flow_type"] == "device"
    return session_data["state"]
