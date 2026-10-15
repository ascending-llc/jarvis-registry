"""
Integration tests for OAuth callback flow with standard token exchange.

Tests the refactored oauth2_callback that always uses OAuth client flow
and registry redirect endpoint that calls /oauth2/token to decode JWT.
"""

import base64
import secrets
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from auth_server.core.config import settings
from auth_server.deps import (
    get_auth_provider,
    get_identity_service,
    get_oauth2_config,
    get_oauth_state_store,
    get_signer,
    get_token_grant_service,
)
from auth_server.providers.google import GoogleDomainNotAllowedError, GoogleEmailNotVerifiedError
from auth_server.server import app
from auth_server.services.token_grant_service import TokenGrantService
from registry_pkgs.core.jwt_tokens import MintedManagedAgentToken
from registry_pkgs.core.jwt_utils import InvalidTokenError
from tests.conftest import test_consent_store, test_pending_consent_store
from tests.support.auth_provider import _mock_entra_provider
from tests.support.oauth_state_store import authorization_codes_storage, test_oauth_state_store

API_PREFIX = "/auth"


@pytest.fixture
def mock_identity_service():
    """Mock identity_service for user_id resolution."""
    mock_service = MagicMock()

    # Default behavior: resolve_or_provision returns a valid user_id
    mock_service.resolve_or_provision = AsyncMock(return_value=SimpleNamespace(id="507f1f77bcf86cd799439011"))

    yield mock_service


@pytest.mark.integration
@pytest.mark.oauth_callback
class TestOAuth2CallbackStandardFlow:
    """Test OAuth2 callback with standard OAuth client flow."""

    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    def test_oauth_callback_always_generates_authorization_code(
        self,
        mock_exchange_token,
        clear_device_storage,
        mock_identity_service,
    ):
        """Test that oauth2_callback always generates authorization code (for both external clients and registry)."""
        # Mock provider token exchange
        mock_exchange_token.return_value = {"access_token": "provider_access_token", "id_token": "provider_id_token"}

        # Mock user info from provider
        user_info = {
            "username": "testuser",
            "email": "test@example.com",
            "name": "Test User",
            "id": "provider-user-123",
            "groups": ["jarvis-registry-admin"],
        }

        oauth2_config = {
            "providers": {
                "entra": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://entra/token",
                    "user_info_url": "http://entra/userinfo",
                    "username_claim": "preferred_username",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": "groups",
                }
            }
        }

        # Mock settings
        with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.registry_app_name = "registry-internal-client"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.auth_server_api_prefix = ""
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"
            mock_settings.oauth2_temp_session_cookie_name = settings.oauth2_temp_session_cookie_name
            mock_settings.oauth2_consent_nonce_cookie_name = settings.oauth2_consent_nonce_cookie_name

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}

            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: _mock_entra_provider(user_info=user_info)

            test_client = TestClient(app)

            # Create a valid session cookie
            session_data = {
                "state": "test-state-123",
                "client_state": None,
                "provider": "entra",
                "redirect_uri": "http://localhost:3000/redirect",
                "client_id": "mock-client-id",
                "code_challenge": "123",
                "code_challenge_method": "S256",
                "client_redirect_uri": "http://localhost:3000/redirect",
            }
            temp_session = test_signer.dumps(session_data)

            # Make callback request
            test_client.cookies.set(settings.oauth2_temp_session_cookie_name, temp_session)
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/entra",
                params={"code": "provider_auth_code", "state": "test-state-123"},
                follow_redirects=False,
            )

            print(response.text)

            # Should redirect with authorization code
            assert response.status_code == 302
            location = response.headers["location"]
            assert "code=" in location
            assert location.startswith("http://localhost:3000/redirect")

            # Extract authorization code
            import urllib.parse

            parsed_url = urllib.parse.urlparse(location)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            auth_code = query_params.get("code", [None])[0]
            assert auth_code is not None

            # Verify code is stored in authorization_codes_storage
            assert auth_code in authorization_codes_storage
            code_data = authorization_codes_storage[auth_code]

            # Verify standard OAuth client flow structure
            assert code_data["client_id"] == "mock-client-id"
            assert code_data["redirect_uri"] == "http://localhost:3000/redirect"
            assert "user_info" in code_data
            assert code_data["user_info"]["username"] == "testuser"
            assert code_data["user_info"]["user_id"] == "507f1f77bcf86cd799439011"
            assert "used" not in code_data

    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    def test_oauth_callback_external_client_with_client_id(
        self,
        mock_exchange_token,
        clear_device_storage,
        mock_identity_service,
    ):
        """Test oauth2_callback with explicit client_id (external OAuth client)."""
        mock_exchange_token.return_value = {"access_token": "provider_access_token", "id_token": "provider_id_token"}

        user_info = {
            "username": "externaluser",
            "email": "external@example.com",
            "name": "External User",
            "id": "provider-user-456",
            "groups": ["jarvis-registry-admin"],
        }

        oauth2_config = {
            "providers": {
                "entra": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://entra/token",
                    "user_info_url": "http://entra/userinfo",
                    "username_claim": "preferred_username",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": "groups",
                }
            }
        }

        with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.auth_server_api_prefix = ""
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"
            mock_settings.oauth2_temp_session_cookie_name = settings.oauth2_temp_session_cookie_name
            mock_settings.oauth2_consent_nonce_cookie_name = settings.oauth2_consent_nonce_cookie_name

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}

            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: _mock_entra_provider(user_info=user_info)

            test_client = TestClient(app)

            # Session with explicit client_id
            session_data = {
                "state": "test-state-456",
                "client_state": "external-state-abc",
                "provider": "entra",
                "redirect_uri": "http://external-app.com/callback",
                "client_id": "external-client-123",
                "client_redirect_uri": "http://external-app.com/callback",
                "code_challenge": "test-challenge",
                "code_challenge_method": "S256",
            }
            temp_session = test_signer.dumps(session_data)

            test_client.cookies.set(settings.oauth2_temp_session_cookie_name, temp_session)
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/entra",
                params={"code": "provider_auth_code", "state": "test-state-456"},
                follow_redirects=False,
            )

            # Should redirect to external client with state
            assert response.status_code == 302
            location = response.headers["location"]
            assert location.startswith("http://external-app.com/callback")
            assert "code=" in location
            assert "state=external-state-abc" in location

            # Extract and verify authorization code
            import urllib.parse

            parsed_url = urllib.parse.urlparse(location)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            auth_code = query_params.get("code", [None])[0]

            code_data = authorization_codes_storage[auth_code]
            assert code_data["client_id"] == "external-client-123"
            assert code_data["redirect_uri"] == "http://external-app.com/callback"
            assert code_data["code_challenge"] == "test-challenge"
            assert code_data["code_challenge_method"] == "S256"

    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    @patch("auth_server.routes.oauth_flow.get_user_info")
    @pytest.mark.parametrize("provider_error", [InvalidTokenError("bad signature"), ValueError("missing id_token")])
    def test_oauth_callback_rejects_provider_errors_entra(
        self,
        mock_get_user_info,
        mock_exchange_token,
        clear_device_storage,
        mock_identity_service,
        provider_error,
    ):
        """Entra invalid token must not fall back to userInfo."""
        mock_exchange_token.return_value = {"access_token": "entra_access", "id_token": "entra_id_token"}

        mock_entra_provider = MagicMock()
        mock_entra_provider.get_user_info = AsyncMock(side_effect=provider_error)

        oauth2_config = {
            "providers": {
                "entra": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://entra/token",
                    "user_info_url": "http://entra/userinfo",
                    "username_claim": "preferred_username",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": "groups",
                }
            }
        }

        with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.auth_server_api_prefix = ""
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"
            mock_settings.oauth2_temp_session_cookie_name = settings.oauth2_temp_session_cookie_name
            mock_settings.oauth2_consent_nonce_cookie_name = settings.oauth2_consent_nonce_cookie_name

            test_signer = URLSafeTimedSerializer("test-secret-key")
            app.dependency_overrides = {}
            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: mock_entra_provider

            test_client = TestClient(app)
            session_data = {
                "state": "test-state-entra-invalid-sig",
                "client_state": None,
                "provider": "entra",
                "redirect_uri": "http://localhost:3000/redirect",
                "client_id": "mock-client-id",
                "code_challenge": "123",
                "code_challenge_method": "S256",
                "client_redirect_uri": "http://localhost:3000/redirect",
            }
            temp_session = test_signer.dumps(session_data)

            test_client.cookies.set(settings.oauth2_temp_session_cookie_name, temp_session)
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/entra",
                params={"code": "entra_code", "state": "test-state-entra-invalid-sig"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert "Something+went+wrong+during+sign-in" in response.headers["location"]
        mock_get_user_info.assert_not_called()
        mock_identity_service.resolve_or_provision.assert_not_awaited()

    def _run_google_gate_callback(
        self,
        error: Exception,
        mock_identity_service,
    ):
        """Drive a Google callback whose get_user_info raises a login-gate error and
        return the 302 response. The login-gate rejection must surface a specific error
        code and never fall back to the generic userInfo path."""
        oauth2_config = {
            "providers": {
                "google": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://google/token",
                    "user_info_url": "http://google/userinfo",
                    "username_claim": "email",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": None,
                }
            }
        }

        mock_google_provider = MagicMock()
        mock_google_provider.get_user_info = AsyncMock(side_effect=error)

        with (
            patch("auth_server.routes.oauth_flow.exchange_code_for_token") as mock_exchange_token,
            patch("auth_server.routes.oauth_flow.get_user_info") as mock_get_user_info,
            patch("auth_server.routes.oauth_flow.settings") as mock_settings,
        ):
            mock_exchange_token.return_value = {"access_token": "google_access", "id_token": "google_id_token"}
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.registry_error_redirect = "http://localhost:3000/error"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.auth_server_api_prefix = ""
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"
            mock_settings.oauth2_temp_session_cookie_name = settings.oauth2_temp_session_cookie_name
            mock_settings.oauth2_consent_nonce_cookie_name = settings.oauth2_consent_nonce_cookie_name

            test_signer = URLSafeTimedSerializer("test-secret-key")
            app.dependency_overrides = {}
            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: mock_google_provider

            test_client = TestClient(app)
            session_data = {
                "state": "test-state-google-gate",
                "client_state": None,
                "provider": "google",
                "redirect_uri": "http://localhost:3000/redirect",
                "client_id": "mock-client-id",
                "code_challenge": "123",
                "code_challenge_method": "S256",
                "client_redirect_uri": "http://localhost:3000/redirect",
            }
            test_client.cookies.set(settings.oauth2_temp_session_cookie_name, test_signer.dumps(session_data))
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/google",
                params={"code": "google_code", "state": "test-state-google-gate"},
                follow_redirects=False,
            )

        mock_get_user_info.assert_not_called()
        return response

    def test_oauth_callback_google_unverified_email_redirects(self, clear_device_storage, mock_identity_service):
        response = self._run_google_gate_callback(
            GoogleEmailNotVerifiedError("email not verified"), mock_identity_service
        )
        assert response.status_code == 302
        assert "Your+Google+email+address+is+not+verified" in response.headers["location"]

    def test_oauth_callback_google_domain_not_allowed_redirects(self, clear_device_storage, mock_identity_service):
        response = self._run_google_gate_callback(
            GoogleDomainNotAllowedError("domain not allowed"), mock_identity_service
        )
        assert response.status_code == 302
        assert "organization+is+not+authorized" in response.headers["location"]

    @staticmethod
    def _google_oauth2_config() -> dict:
        return {
            "providers": {
                "google": {
                    "enabled": True,
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "token_url": "http://google/token",
                    "user_info_url": "http://google/userinfo",
                    "username_claim": "email",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": None,
                    "allowed_hd": "corp.example",
                }
            }
        }

    def _drive_google_callback(
        self,
        mock_google_provider: MagicMock,
        mock_identity_service: MagicMock,
        module_get_user_info: dict[str, Any] | None = None,
        *,
        flow: Literal["browser", "device"] = "browser",
        requested_scope: str | None = None,
    ) -> httpx.Response:
        """Drive a Google callback, optionally using a mocked userinfo fallback response."""
        with (
            patch("auth_server.routes.oauth_flow.exchange_code_for_token") as mock_exchange_token,
            patch("auth_server.routes.oauth_flow.get_user_info") as mock_get_user_info,
            patch("auth_server.routes.oauth_flow.settings") as mock_settings,
        ):
            mock_exchange_token.return_value = {"access_token": "google_access", "id_token": "google_id_token"}
            if module_get_user_info is not None:
                mock_get_user_info.return_value = module_get_user_info
            mock_settings.registry_error_redirect = "http://localhost:3000/login"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.auth_server_api_prefix = ""
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"
            mock_settings.oauth2_temp_session_cookie_name = settings.oauth2_temp_session_cookie_name
            mock_settings.oauth2_consent_nonce_cookie_name = settings.oauth2_consent_nonce_cookie_name

            test_signer = URLSafeTimedSerializer("test-secret-key")
            app.dependency_overrides = {}
            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = self._google_oauth2_config
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: mock_google_provider

            test_client = TestClient(app)
            session_data = {
                "state": "test-state-google-flow",
                "client_state": None,
                "provider": "google",
                "redirect_uri": "http://localhost:3000/redirect",
                "client_id": "mock-client-id",
                "code_challenge": "123",
                "code_challenge_method": "S256",
                "client_redirect_uri": "http://localhost:3000/redirect",
                "requested_scope": requested_scope,
            }
            if flow == "device":
                device_code = "google-device-code"
                test_oauth_state_store.save_device_code(
                    device_code,
                    {"status": "pending", "client_id": "mock-client-id", "scope": requested_scope},
                    ttl_seconds=600,
                )
                session_data.update(flow_type="device", device_code=device_code)
            test_client.cookies.set(settings.oauth2_temp_session_cookie_name, test_signer.dumps(session_data))
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/google",
                params={"code": "google_code", "state": "test-state-google-flow"},
                follow_redirects=False,
            )
            if module_get_user_info is not None:
                mock_get_user_info.assert_awaited_once_with(
                    "google_access", self._google_oauth2_config()["providers"]["google"]
                )
            else:
                mock_get_user_info.assert_not_awaited()
            return response

    def test_oauth_callback_zero_scope_browser_login_rejected(self, clear_device_storage, mock_identity_service):
        """A browser login that resolves zero scopes must be rejected with a human-readable
        message and must never mint a session cookie."""
        mock_google_provider = MagicMock()
        mock_google_provider.get_user_info = AsyncMock(
            return_value={
                "username": "user@corp.example",
                "email": "user@corp.example",
                "name": "User",
                "id": "sub-1",
                "groups": [],
                "issuer": "https://accounts.google.com",
                "subject": "sub-1",
                "object_id": None,
                "email_trusted": True,
            }
        )

        response = self._drive_google_callback(mock_google_provider, mock_identity_service)

        assert response.status_code == 302
        assert "no+permissions+are+currently+assigned" in response.headers["location"]
        assert "localhost:3000/login" in response.headers["location"]
        # Did not proceed to _finish_oauth2_callback (would redirect to the client /redirect).
        assert "/redirect" not in response.headers["location"]
        assert "jarvis_registry_session" not in response.cookies
        mock_identity_service.resolve_or_provision.assert_not_awaited()
        # The single-use temp OAuth session cookie must be cleared on this terminal failure.
        assert settings.oauth2_temp_session_cookie_name in response.headers.get("set-cookie", "")

    def test_oauth_callback_google_fallback_enforces_email_verified(self, clear_device_storage, mock_identity_service):
        """When the generic userinfo fallback is taken for Google, an unverified-email userinfo
        response must raise GoogleEmailNotVerifiedError instead of bypassing the login gate."""
        mock_google_provider = MagicMock()
        # A bare ValueError is not one of the six re-raised types → hits the userinfo fallback.
        mock_google_provider.get_user_info = AsyncMock(side_effect=ValueError("JWKS unavailable"))

        response = self._drive_google_callback(
            mock_google_provider,
            mock_identity_service,
            module_get_user_info={"sub": "google-subject", "email": "user@corp.example", "email_verified": False},
        )

        assert response.status_code == 302
        assert "Your+Google+email+address+is+not+verified" in response.headers["location"]

    def test_oauth_callback_google_fallback_enforces_hd(self, clear_device_storage, mock_identity_service):
        """When the generic userinfo fallback is taken for Google, a mismatched hd must raise
        GoogleDomainNotAllowedError when allowed_hd is configured."""
        mock_google_provider = MagicMock()
        mock_google_provider.get_user_info = AsyncMock(side_effect=ValueError("JWKS unavailable"))

        response = self._drive_google_callback(
            mock_google_provider,
            mock_identity_service,
            module_get_user_info={
                "sub": "google-subject",
                "email": "user@attacker.com",
                "email_verified": True,
                "hd": "attacker.com",
            },
        )

        assert response.status_code == 302
        assert "organization+is+not+authorized" in response.headers["location"]

    @pytest.mark.parametrize("flow", ["browser", "device"])
    @pytest.mark.parametrize("requested_scope", [None, "servers-read"])
    def test_google_userinfo_fallback_without_groups_rejects_login(
        self,
        clear_device_storage,
        mock_identity_service: MagicMock,
        flow: Literal["browser", "device"],
        requested_scope: str | None,
    ) -> None:
        """Standard Google userinfo carries identity claims, not Workspace authorization groups."""
        provider = MagicMock()
        provider.get_user_info = AsyncMock(side_effect=ValueError("JWKS unavailable"))
        test_consent_store.default_client_consent = False

        response = self._drive_google_callback(
            provider,
            mock_identity_service,
            module_get_user_info={
                "sub": "123456789012345678901",
                "email": "user@corp.example",
                "email_verified": True,
                "hd": "corp.example",
                "name": "Test User",
            },
            flow=flow,
            requested_scope=requested_scope,
        )

        mock_identity_service.resolve_or_provision.assert_not_awaited()
        assert not authorization_codes_storage
        assert not test_oauth_state_store.refresh_tokens_storage
        assert not test_pending_consent_store.pending
        assert not test_consent_store.client_consents
        assert not test_consent_store.server_consents
        cookies = SimpleCookie()
        cookies.load(response.headers["set-cookie"])
        assert cookies[settings.oauth2_temp_session_cookie_name]["max-age"] == "0"

        if flow == "device":
            assert response.status_code == 400
            device = test_oauth_state_store.get_device_code("google-device-code")
            assert device["status"] == "scope_denied"
            assert device["error_description"] == (
                "Requested scopes are not available for this user"
                if requested_scope
                else "No permissions are currently assigned to this account"
            )
            assert "mapped_user" not in device
            assert "resolved_scope" not in device
        else:
            assert response.status_code == 302
            location = urlparse(response.headers["location"])
            params = parse_qs(location.query)
            assert "code" not in params
            if requested_scope:
                assert location.path == "/redirect"
                assert params["error"] == ["invalid_scope"]
                assert params["error_description"] == ["Requested scopes are not available for this user"]
            else:
                assert location.path == "/login"
                assert "no permissions are currently assigned" in params["error"][0]

    def test_google_userinfo_fallback_maps_identity_with_synthetic_groups(
        self, clear_device_storage, mock_identity_service: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Synthetic groups isolate identity mapping; they do not model a real Google response."""
        sensitive = "private-fallback@example.com bearer-credential"
        provider = MagicMock()
        provider.get_user_info = AsyncMock(side_effect=ValueError(sensitive))
        response = self._drive_google_callback(
            provider,
            mock_identity_service,
            module_get_user_info={
                "sub": "google-subject",
                "email": " User@Corp.Example ",
                "email_verified": True,
                "hd": "corp.example",
                "groups": ["jarvis-registry-admin"],
            },
        )
        assert response.status_code == 302
        assert "code=" in response.headers["location"]
        mock_identity_service.resolve_or_provision.assert_awaited_once()
        identity = mock_identity_service.resolve_or_provision.await_args.args[0]
        assert identity.provider == "google"
        assert identity.issuer == "https://accounts.google.com"
        assert identity.subject == "google-subject"
        assert identity.object_id is None
        assert identity.email == "user@corp.example"
        assert identity.email_trusted is True
        stored = next(iter(authorization_codes_storage.values()))["user_info"]
        assert stored["email"] == stored["username"] == "user@corp.example"
        assert stored["user_id"] == "507f1f77bcf86cd799439011"
        assert sensitive not in caplog.text
        assert "provider=google error_type=ValueError" in caplog.text

    @pytest.mark.parametrize("field", ["sub", "email"])
    @pytest.mark.parametrize("invalid", [None, "", "   ", 42])
    def test_google_userinfo_fallback_rejects_invalid_identity(
        self, clear_device_storage, mock_identity_service: MagicMock, field: str, invalid: str | int | None
    ) -> None:
        provider = MagicMock()
        provider.get_user_info = AsyncMock(side_effect=ValueError("JWKS unavailable"))
        userinfo = {
            "sub": "google-subject",
            "email": "user@corp.example",
            "email_verified": True,
            "hd": "corp.example",
            field: invalid,
        }
        response = self._drive_google_callback(provider, mock_identity_service, module_get_user_info=userinfo)
        assert response.status_code == 302
        location = urlparse(response.headers["location"])
        assert location.path == "/login"
        assert parse_qs(location.query)["error"] == [
            "Your Google email address is not verified. Please verify it with Google and try again."
            if field == "email"
            else "Something went wrong during sign-in. Please try again."
        ]
        mock_identity_service.resolve_or_provision.assert_not_awaited()
        assert not authorization_codes_storage

    def test_google_userinfo_fallback_preserves_opaque_subject_with_synthetic_groups(
        self, clear_device_storage, mock_identity_service: MagicMock
    ) -> None:
        """Synthetic groups allow the opaque subject to reach the identity service."""
        provider = MagicMock()
        provider.get_user_info = AsyncMock(side_effect=ValueError("JWKS unavailable"))
        subject = " Google-Subject-With-Case "
        response = self._drive_google_callback(
            provider,
            mock_identity_service,
            module_get_user_info={
                "sub": subject,
                "email": "user@corp.example",
                "email_verified": True,
                "hd": "corp.example",
                "groups": ["jarvis-registry-admin"],
            },
        )
        assert "code=" in response.headers["location"]
        assert mock_identity_service.resolve_or_provision.await_args.args[0].subject == subject

    @patch("auth_server.routes.oauth_flow.exchange_code_for_token")
    @patch("auth_server.routes.oauth_flow.get_user_info")
    def test_oauth_callback_new_user_is_provisioned(
        self,
        mock_get_user_info,
        mock_exchange,
        clear_device_storage,
    ):
        """First login stores the provisioned user ID and verified identity in the authorization code."""
        mock_exchange.return_value = {"access_token": "token", "id_token": "id"}
        user_info = {
            "username": "newuser",
            "email": "new@example.com",
            "name": "New User",
            "id": "new-user",
            "groups": ["jarvis-registry-admin"],
        }

        oauth2_config = {
            "providers": {
                "entra": {
                    "enabled": True,
                    "client_id": "test",
                    "client_secret": "secret",
                    "token_url": "http://entra/token",
                    "user_info_url": "http://entra/userinfo",
                    "username_claim": "preferred_username",
                    "email_claim": "email",
                    "name_claim": "name",
                    "groups_claim": "groups",
                }
            }
        }

        with patch("auth_server.routes.oauth_flow.settings") as mock_settings:
            mock_settings.registry_url = "http://localhost:3000"
            mock_settings.auth_server_external_url = "http://localhost:8888"
            mock_settings.auth_server_url = "http://localhost:8888"
            mock_settings.auth_server_api_prefix = ""
            mock_settings.oauth_session_ttl_seconds = 600
            mock_settings.secret_key = "test-secret-key"
            mock_settings.oauth2_temp_session_cookie_name = settings.oauth2_temp_session_cookie_name
            mock_settings.oauth2_consent_nonce_cookie_name = settings.oauth2_consent_nonce_cookie_name

            test_signer = URLSafeTimedSerializer("test-secret-key")

            app.dependency_overrides = {}

            mock_identity_service = MagicMock()
            mock_identity_service.resolve_or_provision = AsyncMock(return_value=SimpleNamespace(id="new-user-id"))

            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_oauth2_config] = lambda: oauth2_config
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_signer] = lambda: test_signer
            app.dependency_overrides[get_auth_provider] = lambda: _mock_entra_provider(user_info=user_info)

            test_client = TestClient(app)

            session_data = {
                "state": "test-state",
                "client_state": None,
                "provider": "entra",
                "redirect_uri": "http://localhost:3000/redirect",
                "client_id": "mock-client-id",
                "code_challenge": "123",
                "code_challenge_method": "S256",
                "client_redirect_uri": "http://localhost:3000/redirect",
            }
            temp_session = test_signer.dumps(session_data)

            test_client.cookies.set(settings.oauth2_temp_session_cookie_name, temp_session)
            response = test_client.get(
                f"{API_PREFIX}/oauth2/callback/entra",
                params={"code": "code", "state": "test-state"},
                follow_redirects=False,
            )

        import urllib.parse

        assert response.status_code == 302
        location = response.headers["location"]
        parsed_url = urllib.parse.urlparse(location)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        auth_code = query_params.get("code", [None])[0]

        code_data = authorization_codes_storage[auth_code]
        assert code_data["user_info"]["user_id"] == "new-user-id"
        assert code_data["user_info"]["subject"] == "provider-subject-123"
        mock_identity_service.resolve_or_provision.assert_awaited_once()
        mock_get_user_info.assert_not_called()


@pytest.mark.integration
@pytest.mark.oauth_token
class TestOAuth2TokenEndpoint:
    """Test /oauth2/token endpoint with authorization code grant."""

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_with_authorization_code(self, clear_device_storage, mock_identity_service, content_type):
        """Test token endpoint exchanges authorization code for JWT with user_id."""
        # Create authorization code directly
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {
                "user_id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "groups": ["user-group"],
                "idp_id": "provider-sub-123",
            },
            "client_id": "mcp-client-test",
            "expires_at": current_time + 600,
            "used": False,
            "redirect_uri": "http://localhost/callback",
            "resolved_scope": ["mcp-proxy-ops"],
            "resource": None,
            "created_at": current_time,
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": "mcp-client-test",
            "redirect_uri": "http://localhost/callback",
        }

        # Exchange code for token
        with patch("auth_server.services.token_grant_service.mint_managed_agent_token_with_scope") as mock_mint_token:
            mock_mint_token.return_value = MintedManagedAgentToken("mock-jwt-token-with-user-id", "mcp-proxy-ops")

            app.dependency_overrides = {}

            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
                test_oauth_state_store, test_consent_store
            )

            test_client = TestClient(app)

            kwargs = {"json": payload} if content_type == "json" else {"data": payload}
            response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

            assert response.status_code == 200
            token_data = response.json()

            assert token_data["access_token"] == "mock-jwt-token-with-user-id"
            assert token_data["token_type"] == "Bearer"
            assert "expires_in" in token_data
            assert "refresh_token" in token_data

            # Verify managed-agent token was minted with resolved user identity
            assert mock_mint_token.call_args.kwargs["subject"] == "testuser"
            assert mock_mint_token.call_args.kwargs["client_id"] == "mcp-client-test"
            token_claims = mock_mint_token.call_args.kwargs["extra_claims"]
            assert token_claims["user_id"] == "507f1f77bcf86cd799439011"
            assert token_claims["groups"] == ["user-group"]
            assert mock_mint_token.call_args.kwargs["requested_scopes"] == ["mcp-proxy-ops"]
            assert token_claims["token_use"] == "access"
            assert token_claims["auth_provider"] is not None

            # Code should be deleted after successful exchange
            assert auth_code not in authorization_codes_storage

    def test_token_endpoint_rejects_invalid_client_secret(self, clear_device_storage, mock_identity_service):
        """Test client_secret_post DCR clients must provide the registered secret."""
        client_id = "secret-client"
        test_oauth_state_store.save_client(
            client_id,
            {
                "client_id": client_id,
                "client_secret": "correct-secret",
                "token_endpoint_auth_method": "client_secret_post",
                "redirect_uris": ["http://localhost/callback"],
            },
        )

        auth_code = secrets.token_urlsafe(32)
        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {
                "user_id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "groups": [],
            },
            "client_id": client_id,
            "expires_at": int(__import__("time").time()) + 600,
            "redirect_uri": "http://localhost/callback",
            "resolved_scope": ["servers-read"],
            "resource": None,
            "created_at": int(__import__("time").time()),
        }

        app.dependency_overrides = {}
        app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
        app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
        app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
            test_oauth_state_store, test_consent_store
        )

        test_client = TestClient(app)
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": client_id,
                "client_secret": "wrong-secret",
                "redirect_uri": "http://localhost/callback",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client"
        assert auth_code in authorization_codes_storage

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_quick_suite_basic_auth_fallback(
        self,
        clear_device_storage,
        mock_identity_service,
        content_type,
    ):
        """Test Quick Suite compatibility: resolve client_id/client_secret from Basic auth when body omits them."""
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {
                "user_id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "groups": ["user-group"],
                "idp_id": "provider-sub-123",
            },
            "client_id": "mcp-client-test",
            "expires_at": current_time + 600,
            "used": False,
            "redirect_uri": "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback",
            "resource": None,
            "created_at": current_time,
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": "test-code-verifier",
            "redirect_uri": "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback",
        }

        basic_auth = base64.b64encode(b"mcp-client-test:test-secret").decode("ascii")

        with patch("auth_server.services.token_grant_service.mint_managed_agent_token_with_scope") as mock_mint_token:
            mock_mint_token.return_value = MintedManagedAgentToken("mock-jwt-token-with-user-id", "mcp-proxy-ops")

            app.dependency_overrides = {}
            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
                test_oauth_state_store, test_consent_store
            )

            test_client = TestClient(app)

            headers = {"Authorization": f"Basic {basic_auth}"}
            kwargs = (
                {"json": payload, "headers": headers}
                if content_type == "json"
                else {"data": payload, "headers": headers}
            )
            response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

            assert response.status_code == 200

            token_data = response.json()
            assert token_data["access_token"] == "mock-jwt-token-with-user-id"

            assert mock_mint_token.call_args.kwargs["client_id"] == "mcp-client-test"
            token_claims = mock_mint_token.call_args.kwargs["extra_claims"]
            assert token_claims["user_id"] == "507f1f77bcf86cd799439011"
            assert token_claims["token_use"] == "access"

            assert auth_code not in authorization_codes_storage

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_non_quick_suite_basic_auth_no_fallback(
        self,
        clear_device_storage,
        mock_identity_service,
        content_type,
    ):
        """Test non-Quick Suite clients using Basic auth do not get client_id fallback."""
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {
                "user_id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "groups": ["user-group"],
                "idp_id": "provider-sub-123",
            },
            "client_id": "mcp-client-test",
            "expires_at": current_time + 600,
            "used": False,
            "redirect_uri": "https://example.com/oauth/callback",
            "resource": None,
            "created_at": current_time,
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": "test-code-verifier",
            "redirect_uri": "https://example.com/oauth/callback",
        }

        basic_auth = base64.b64encode(b"mcp-client-test:test-secret").decode("ascii")

        with patch("auth_server.services.token_grant_service.mint_managed_agent_token_with_scope") as mock_mint_token:
            app.dependency_overrides = {}
            app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
            app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
            app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
                test_oauth_state_store, test_consent_store
            )

            test_client = TestClient(app)

            headers = {"Authorization": f"Basic {basic_auth}"}
            kwargs = (
                {"json": payload, "headers": headers}
                if content_type == "json"
                else {"data": payload, "headers": headers}
            )
            response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

            # Should reject since client_id fallback only applies to Quick Suite hosts
            assert response.status_code == 400
            assert response.json()["error"] == "invalid_request"
            assert response.json()["error_description"] == "client_id is required"
            mock_mint_token.assert_not_called()

            # Request should fail before code exchange, so code remains stored.
            assert auth_code in authorization_codes_storage

    @pytest.mark.parametrize("content_type", ["form", "json"])
    def test_token_endpoint_code_already_used(self, clear_device_storage, mock_identity_service, content_type):
        """Test token endpoint rejects an authorization code consumed by a prior request."""
        auth_code = secrets.token_urlsafe(32)
        current_time = int(__import__("time").time())

        authorization_codes_storage[auth_code] = {
            "token_data": {},
            "user_info": {"user_id": "507f1f77bcf86cd799439011", "username": "testuser", "groups": []},
            "client_id": "mcp-client-test",
            "expires_at": current_time + 600,
            "redirect_uri": "http://localhost/callback",
            "created_at": current_time,
            "resolved_scope": ["mcp-proxy-ops"],
        }

        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": "mcp-client-test",
            "redirect_uri": "http://localhost/callback",
        }

        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
        app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
        app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
            test_oauth_state_store, test_consent_store
        )

        test_client = TestClient(app)

        kwargs = {"json": payload} if content_type == "json" else {"data": payload}
        with patch("auth_server.services.token_grant_service.mint_managed_agent_token_with_scope") as mock_mint_token:
            mock_mint_token.return_value = MintedManagedAgentToken("mock-jwt-token", "mcp-proxy-ops")
            first_response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)
            response = test_client.post(f"{API_PREFIX}/oauth2/token", **kwargs)

        assert first_response.status_code == 200
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"

        # Code should be deleted
        assert auth_code not in authorization_codes_storage

    def test_token_endpoint_unsupported_content_type(self, mock_identity_service):
        """Test token endpoint rejects unsupported content-type with 415."""
        app.dependency_overrides = {}

        app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
        app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
        app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
            test_oauth_state_store, test_consent_store
        )

        test_client = TestClient(app)

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            content="grant_type=authorization_code&client_id=test-client",
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 415

    @pytest.mark.parametrize(
        "content, expected_description",
        [
            ('["not", "an", "object"]', "JSON body must be an object"),
            ('{"grant_type":', "JSON body is malformed"),
        ],
    )
    def test_token_endpoint_rejects_invalid_json_shape(
        self,
        mock_identity_service,
        content: str,
        expected_description: str,
    ):
        app.dependency_overrides = {}
        app.dependency_overrides[get_oauth_state_store] = lambda: test_oauth_state_store
        app.dependency_overrides[get_identity_service] = lambda: mock_identity_service
        app.dependency_overrides[get_token_grant_service] = lambda: TokenGrantService(
            test_oauth_state_store, test_consent_store
        )
        test_client = TestClient(app)

        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            content=content,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": "invalid_request",
            "error_description": expected_description,
        }

    def test_token_endpoint_rejects_non_string_fields(self, test_client: TestClient):
        response = test_client.post(
            f"{API_PREFIX}/oauth2/token",
            json={
                "grant_type": "authorization_code",
                "client_id": ["mcp-client-test"],
                "code": "code",
                "redirect_uri": "https://example.com/callback",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"
