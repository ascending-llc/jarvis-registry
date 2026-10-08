import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from registry_pkgs.oauth.headers import (
    HeaderBuildConfig,
    build_complete_headers_for_server,
)

_CFG = HeaderBuildConfig(
    registry_app_name="jarvis-registry",
    redis_key_prefix="jarvis-registry",
    jwt_signing_config=SimpleNamespace(),
    encryption_key=None,
)


async def _bch(*args, cfg=_CFG, **kwargs):
    """Call the real header builder with a default test HeaderBuildConfig."""
    return await build_complete_headers_for_server(*args, cfg=cfg, **kwargs)


class TestBuildCompleteHeaders:
    """Test suite for build_complete_headers_for_server function."""

    @pytest.fixture(autouse=True)
    def mock_sign_agentcore_jwt(self):
        """Patch sign_agentcore_jwt so tests without explicit override don't need a real RSA private key."""
        with patch("registry_pkgs.oauth.headers.sign_agentcore_jwt", return_value="mock-agentcore-jwt"):
            yield

    @pytest.fixture
    def mock_oauth_server(self):
        """Create mock OAuth server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "oauth-server"
        server.config = {
            "requiresOAuth": True,
            "oauth": {
                "authorization_url": "https://oauth.example.com/authorize",
                "token_url": "https://oauth.example.com/token",
            },
        }
        return server

    @pytest.fixture
    def mock_apikey_server(self):
        """Create mock API key server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "apikey-server"
        server.config = {"apiKey": {"key": "test-api-key-123", "authorization_type": "bearer"}}
        return server

    @pytest.fixture
    def mock_basic_auth_server(self):
        """Create mock Basic auth server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "basic-auth-server"
        server.config = {"apiKey": {"key": "username:password", "authorization_type": "basic"}}
        return server

    @pytest.fixture
    def mock_custom_auth_server(self):
        """Create mock custom auth server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "custom-auth-server"
        server.config = {
            "apiKey": {"key": "custom-token-xyz", "authorization_type": "custom", "custom_header": "X-API-Key"}
        }
        return server

    @pytest.fixture
    def mock_custom_headers_server(self):
        """Create mock server with custom headers."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "custom-headers-server"
        server.config = {"headers": [{"X-Custom-Header": "value1"}, {"X-Another-Header": "value2"}]}
        return server

    @pytest.fixture
    def mock_agentcore_jwt_server(self):
        """Create mock AgentCore Runtime MCP server with resource-level JWT config."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.id = "server-123"
        server.serverName = "agentcore-server"
        server.config = {
            "runtimeAccess": {
                "mode": "jwt",
                "jwt": {
                    "discoveryUrl": "https://agentcore-issuer.example.com/.well-known/openid-configuration",
                    "audiences": ["agentcore-runtime", "secondary-audience"],
                    "allowedClients": [" jarvis-registry "],
                    "allowedScopes": [" tools:read ", " tools:call "],
                    "customClaims": {"tenant": " prod "},
                },
            }
        }
        return server

    @pytest.fixture
    def jwt_signing_config(self):
        """Create signing config with a real test key so JWT claims can be decoded."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        from registry_pkgs.core.config import JwtSigningConfig

        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_key = rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        return JwtSigningConfig(
            jwt_private_key=private_key,
            jwt_issuer="https://registry.example.com",
            jwt_self_signed_kid="server-service-test-key",
            jwt_audience="jarvis-services",
            registry_app_name="jarvis-registry",
        )

    @pytest.mark.asyncio
    async def test_agentcore_jwt_uses_server_runtime_access_claims(
        self,
        mock_agentcore_jwt_server,
        jwt_signing_config,
    ):
        """AgentCore MCP proxy JWT derives iss/aud from the specific server config."""
        from unittest.mock import patch

        from registry_pkgs.core.agentcore_jwt import mint_agentcore_runtime_jwt
        from registry_pkgs.core.jwt_utils import decode_jwt_unverified

        cfg = HeaderBuildConfig(
            registry_app_name="jarvis-registry",
            redis_key_prefix="test-registry",
            jwt_signing_config=jwt_signing_config,
            encryption_key=None,
        )
        with (
            patch("registry_pkgs.oauth.headers.decrypt_auth_fields", return_value=mock_agentcore_jwt_server.config),
            patch("registry_pkgs.oauth.headers.sign_agentcore_jwt") as mock_sign,
        ):
            mock_sign.side_effect = lambda *args, **kwargs: mint_agentcore_runtime_jwt(
                kwargs.get("runtime_jwt_config") or (args[0] if args else None),
                subject=kwargs.get("signing", jwt_signing_config).registry_app_name,
                signing=kwargs.get("signing", jwt_signing_config),
                expires_in_seconds=3600,
            )

            headers = await _bch(Mock(), mock_agentcore_jwt_server, None, cfg=cfg)

        token = headers["Authorization"].split(" ", 1)[1]
        claims = decode_jwt_unverified(token)
        assert claims["iss"] == "https://agentcore-issuer.example.com"
        assert claims["aud"] == "agentcore-runtime"
        assert claims["sub"] == "jarvis-registry"
        assert claims["client_id"] == "jarvis-registry"
        assert claims["scope"] == "tools:read tools:call"
        assert claims["tenant"] == "prod"

    @pytest.mark.asyncio
    async def test_oauth_server_success(self, mock_oauth_server):
        """Test OAuth server returns valid access token."""
        from unittest.mock import AsyncMock

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("access-token-123", None, None))

            state_metadata = {"client_branding": "vscode"}

            headers = await _bch(
                oauth_service,
                mock_oauth_server,
                "user-123",
                state_metadata=state_metadata,
            )

            assert headers["Authorization"] == "Bearer access-token-123"
            assert headers["Content-Type"] == "application/json"
            assert headers["Accept"] == "application/json"
            oauth_service.get_valid_access_token.assert_called_once_with(
                user_id="user-123", server=mock_oauth_server, state_metadata=state_metadata, interactive=True
            )

    @pytest.mark.asyncio
    async def test_oauth_server_prefers_injected_oauth_service(self, mock_oauth_server):
        """Test OAuth header building uses injected oauth_service before compat getter."""
        from unittest.mock import AsyncMock

        injected_oauth_service = AsyncMock()
        injected_oauth_service.get_valid_access_token = AsyncMock(return_value=("access-token-456", None, None))

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            headers = await _bch(
                injected_oauth_service,
                mock_oauth_server,
                "user-123",
            )

            assert headers["Authorization"] == "Bearer access-token-456"
            injected_oauth_service.get_valid_access_token.assert_called_once_with(
                user_id="user-123",
                server=mock_oauth_server,
                state_metadata=None,
                interactive=True,
            )

    @pytest.mark.asyncio
    async def test_oauth_server_missing_user_id(self, mock_oauth_server):
        """Test OAuth server raises error when user_id is missing."""
        from registry_pkgs.oauth.errors import MissingUserIdError

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            with pytest.raises(MissingUserIdError) as exc_info:
                await _bch(Mock(), mock_oauth_server, None)

            assert "User ID required" in str(exc_info.value)
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_oauth_server_reauth_required(self, mock_oauth_server):
        """Test OAuth server raises error when re-authentication is required."""
        from unittest.mock import AsyncMock

        from registry_pkgs.oauth.errors import OAuthReAuthRequiredError

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(
                return_value=(None, "https://oauth.example.com/authorize", None)
            )

            with pytest.raises(OAuthReAuthRequiredError) as exc_info:
                await _bch(oauth_service, mock_oauth_server, "user-123")

            assert "re-authentication required" in str(exc_info.value).lower()
            assert exc_info.value.auth_url == "https://oauth.example.com/authorize"
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_oauth_server_token_error(self, mock_oauth_server):
        """Test OAuth server raises error when token retrieval fails."""
        from unittest.mock import AsyncMock

        from registry_pkgs.oauth.errors import OAuthTokenError

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=(None, None, "Token refresh failed"))

            with pytest.raises(OAuthTokenError) as exc_info:
                await _bch(oauth_service, mock_oauth_server, "user-123")

            assert "OAuth token error" in str(exc_info.value)
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_oauth_server_no_token(self, mock_oauth_server):
        """Test OAuth server raises error when no token available."""
        from unittest.mock import AsyncMock

        from registry_pkgs.oauth.errors import OAuthTokenError

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=(None, None, None))

            with pytest.raises(OAuthTokenError) as exc_info:
                await _bch(oauth_service, mock_oauth_server, "user-123")

            assert "No valid OAuth token" in str(exc_info.value)
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_apikey_bearer_auth(self, mock_apikey_server):
        """Test API key with Bearer authorization."""

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_apikey_server.config

            headers = await _bch(Mock(), mock_apikey_server, None)

            assert headers["Authorization"] == "Bearer test-api-key-123"
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_apikey_basic_auth(self, mock_basic_auth_server):
        """Test API key with Basic authorization."""

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_basic_auth_server.config

            headers = await _bch(Mock(), mock_basic_auth_server, None)

            # Basic auth should be base64 encoded
            assert headers["Authorization"].startswith("Basic ")
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_apikey_custom_auth(self, mock_custom_auth_server):
        """Test API key with custom header."""

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_custom_auth_server.config

            headers = await _bch(Mock(), mock_custom_auth_server, None)

            assert headers["X-API-Key"] == "custom-token-xyz"
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_custom_headers_only(self, mock_custom_headers_server):
        """Test server with only custom headers."""

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_custom_headers_server.config

            headers = await _bch(Mock(), mock_custom_headers_server, None)

            assert headers["X-Custom-Header"] == "value1"
            assert headers["X-Another-Header"] == "value2"
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_custom_header_logs_keys_without_values(self, mock_custom_headers_server, caplog):
        """Test custom header logs expose keys but never decrypted values."""
        secret_value = "sensitive-header-value"
        mock_custom_headers_server.config = {"headers": [{"Authorization": secret_value}]}
        caplog.set_level(logging.DEBUG, logger="registry_pkgs.oauth.headers")

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_custom_headers_server.config

            await _bch(Mock(), mock_custom_headers_server, None)

        assert "custom-headers-server" in caplog.text
        assert "Authorization" in caplog.text
        assert secret_value not in caplog.text

    @pytest.mark.asyncio
    async def test_oauth_with_custom_headers(self, mock_oauth_server):
        """Test OAuth server also processes custom headers."""
        from unittest.mock import AsyncMock

        # Add custom headers to OAuth config
        mock_oauth_server.config["headers"] = [{"X-Custom-Header": "custom-value"}]

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("access-token", None, None))

            headers = await _bch(
                oauth_service,
                mock_oauth_server,
                "user-123",
            )

            # Should have both OAuth and custom headers
            assert headers["Authorization"] == "Bearer access-token"
            assert headers["X-Custom-Header"] == "custom-value"

    @pytest.mark.asyncio
    async def test_no_auth_returns_base_headers(self):
        """Test server with no authentication returns base MCP headers."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "no-auth-server"
        server.config = {}

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = {}

            headers = await _bch(Mock(), server, None)

            assert headers["Content-Type"] == "application/json"
            assert headers["Accept"] == "application/json"
            # User-Agent is now set to registry_app_name (jarvis-registry-client)
            assert "User-Agent" in headers
            assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_oauth_overrides_custom_authorization_header(self):
        """Test OAuth Bearer token overrides custom Authorization header."""
        from unittest.mock import AsyncMock

        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "oauth-priority-server"
        server.config = {
            "requiresOAuth": True,
            "oauth": {
                "authorizationUrl": "https://oauth.example.com/authorize",
                "tokenUrl": "https://oauth.example.com/token",
            },
            "headers": [{"Authorization": "Bearer custom-should-be-overridden"}],
        }

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("oauth-token-wins", None, None))

            headers = await _bch(oauth_service, server, "user-123")

            # OAuth token should override custom Authorization header
            assert headers["Authorization"] == "Bearer oauth-token-wins"

    @pytest.mark.asyncio
    async def test_apikey_overrides_custom_authorization_header(self):
        """Test API key overrides custom Authorization header."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "apikey-priority-server"
        server.config = {
            "apiKey": {"key": "apikey-token-wins", "authorization_type": "bearer"},
            "headers": [{"Authorization": "Bearer custom-should-be-overridden"}],
        }

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # API key should override custom Authorization header
            assert headers["Authorization"] == "Bearer apikey-token-wins"

    @pytest.mark.asyncio
    async def test_custom_headers_added_first_for_oauth(self):
        """Test custom headers are added before OAuth processing (lowest priority)."""
        from unittest.mock import AsyncMock

        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "oauth-custom-order"
        server.config = {
            "requiresOAuth": True,
            "oauth": {"authorizationUrl": "https://oauth.example.com/authorize"},
            "headers": [
                {"X-Custom-1": "value1"},
                {"X-Custom-2": "value2"},
                {"Content-Type": "application/custom"},  # Will override base header
            ],
        }

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("oauth-token", None, None))

            headers = await _bch(oauth_service, server, "user-123")

            # OAuth Authorization should be present
            assert headers["Authorization"] == "Bearer oauth-token"

            # Custom headers should be present
            assert headers["X-Custom-1"] == "value1"
            assert headers["X-Custom-2"] == "value2"

            # Custom Content-Type should override base header
            assert headers["Content-Type"] == "application/custom"

    @pytest.mark.asyncio
    async def test_custom_headers_added_first_for_apikey(self):
        """Test custom headers are added before API key processing (lowest priority)."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "apikey-custom-order"
        server.config = {
            "apiKey": {"key": "test-key", "authorization_type": "bearer"},
            "headers": [{"X-App-Id": "app-123"}, {"Authorization": "Bearer should-be-overridden"}],
        }

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # API key should override custom Authorization
            assert headers["Authorization"] == "Bearer test-key"

            # Custom non-auth headers should be present
            assert headers["X-App-Id"] == "app-123"

    @pytest.mark.asyncio
    async def test_custom_headers_dict_format(self):
        """Test custom headers provided as dict are supported."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "dict-headers-server"
        server.config = {
            "headers": {
                "X-Trace-Id": "trace-123",
                "Accept": ["application/json", "application/xml"],
            }
        }

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            assert headers["X-Trace-Id"] == "trace-123"
            assert headers["Accept"] == "application/json, application/xml"


class TestOAuthMetadataNotMerged:
    """config.oauthMetadata is no longer read or rewritten; the OAuth service resolves everything."""

    @pytest.mark.asyncio
    async def test_oauth_headers_leave_config_untouched(self):
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "oauth-server"
        server.config = {
            "requiresOAuth": True,
            "oauth": {"client_id": "abc", "client_secret": "plain-secret"},
            "oauthMetadata": {"issuer": "https://stale.example.com"},
        }
        original_config = {
            key: dict(value) if isinstance(value, dict) else value for key, value in server.config.items()
        }

        oauth_service = AsyncMock()
        oauth_service.get_valid_access_token = AsyncMock(return_value=("access-token-123", None, None))

        headers = await _bch(oauth_service, server, "user-123")

        assert headers["Authorization"] == "Bearer access-token-123"
        assert server.config == original_config
        oauth_service.get_valid_access_token.assert_awaited_once_with(
            user_id="user-123", server=server, state_metadata=None, interactive=True
        )


class TestBuildCompleteHeadersExtra:
    """Additional header-building cases (migrated from TestRefreshServerCapabilities)."""

    @pytest.mark.asyncio
    async def test_custom_header_with_list_values(self):
        """Test custom headers with list values are joined correctly."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "list-header-server"
        server.config = {
            "headers": [
                {"Accept": ["application/json", "application/xml"]},
                {"X-Custom-List": ["value1", "value2", "value3"]},
            ]
        }

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # List values should be joined with comma
            assert headers["Accept"] == "application/json, application/xml"
            assert headers["X-Custom-List"] == "value1, value2, value3"

    @pytest.mark.asyncio
    async def test_no_auth_with_custom_headers(self):
        """Test server with no auth but custom headers."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "no-auth-custom-headers"
        server.config = {"headers": [{"X-API-Version": "v2"}, {"X-Request-ID": "req-123"}]}

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # Base headers should be present
            assert headers["Content-Type"] == "application/json"
            assert headers["Accept"] == "application/json"

            # Custom headers should be present
            assert headers["X-API-Version"] == "v2"
            assert headers["X-Request-ID"] == "req-123"

            # No Authorization header
            assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_apikey_basic_auth_pre_encoded(self):
        """Test API key Basic auth with pre-encoded base64."""
        import base64

        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        # Pre-encoded credentials
        encoded_creds = base64.b64encode(b"user:pass").decode()

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "basic-auth-encoded"
        server.config = {"apiKey": {"key": encoded_creds, "authorization_type": "basic"}}

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # Should use the pre-encoded value
            assert headers["Authorization"] == f"Basic {encoded_creds}"

    @pytest.mark.asyncio
    async def test_apikey_basic_auth_not_encoded(self):
        """Test API key Basic auth with plain text credentials (auto-encoding)."""
        import base64

        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "basic-auth-plain"
        server.config = {"apiKey": {"key": "username:password", "authorization_type": "basic"}}

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # Should be auto-encoded
            expected = base64.b64encode(b"username:password").decode()
            assert headers["Authorization"] == f"Basic {expected}"

    @pytest.mark.asyncio
    async def test_apikey_custom_header_missing_custom_header_name(self):
        """Test API key custom auth without custom_header field logs warning."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "custom-auth-missing-header"
        server.config = {
            "apiKey": {
                "key": "custom-token",
                "authorization_type": "custom",
                # Missing "custom_header" field
            }
        }

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # Should not add any custom header
            assert "Authorization" not in headers
            # Only base headers should be present
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_apikey_unknown_authorization_type_defaults_to_bearer(self):
        """Test API key with unknown authorization_type defaults to Bearer."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "unknown-auth-type"
        server.config = {"apiKey": {"key": "test-key", "authorization_type": "unknown_type"}}

        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await _bch(Mock(), server, None)

            # Should default to Bearer
            assert headers["Authorization"] == "Bearer test-key"


class TestBuildAuthenticatedHeadersInteractive:
    """The interactive flag controls how unrefreshable OAuth re-auth surfaces."""

    def _oauth_server(self):
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.serverName = "oauth-server"
        server.config = {"requiresOAuth": True, "oauth": {"authorization_url": "https://x/auth"}}
        return server

    def _auth_context(self):
        return {"user_id": "u-1", "username": "alice"}

    @pytest.mark.asyncio
    async def test_non_interactive_reraises_reauth(self):
        from registry_pkgs.oauth.errors import OAuthReAuthRequiredError
        from registry_pkgs.oauth.headers import build_authenticated_headers

        server = self._oauth_server()
        oauth_service = AsyncMock()
        oauth_service.get_valid_access_token = AsyncMock(
            side_effect=OAuthReAuthRequiredError("reauth", auth_url=None, server_name="oauth-server")
        )
        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields", return_value=server.config):
            with pytest.raises(OAuthReAuthRequiredError):
                await build_authenticated_headers(
                    oauth_service,
                    server,
                    self._auth_context(),
                    effective_scopes=[],
                    cfg=_CFG,
                    interactive=False,
                )

    @pytest.mark.asyncio
    async def test_interactive_converts_reauth_to_elicitation(self):
        from registry_pkgs.core.exceptions import UrlElicitationRequiredException
        from registry_pkgs.oauth.errors import OAuthReAuthRequiredError
        from registry_pkgs.oauth.headers import build_authenticated_headers

        server = self._oauth_server()
        oauth_service = AsyncMock()
        oauth_service.get_valid_access_token = AsyncMock(
            side_effect=OAuthReAuthRequiredError("reauth", auth_url="https://x/url", server_name="oauth-server")
        )
        with patch("registry_pkgs.oauth.headers.decrypt_auth_fields", return_value=server.config):
            with pytest.raises(UrlElicitationRequiredException):
                await build_authenticated_headers(
                    oauth_service,
                    server,
                    self._auth_context(),
                    effective_scopes=[],
                    cfg=_CFG,
                    interactive=True,
                )
