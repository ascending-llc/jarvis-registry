"""Unit tests for OAuthClient (oauth_client.py)."""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from registry.auth.oauth.oauth_client import OAuthClient
from registry.schemas.oauth_schema import (
    MCPOAuthFlowMetadata,
    OAuthClientInformation,
    OAuthMetadata,
    OAuthProtectedResourceMetadata,
)


class TestOAuthClientBasicMethods:
    """Tests for OAuthClient basic methods (PKCE, initialization)"""

    @pytest.fixture
    def oauth_client(self):
        """Create OAuthClient instance"""
        return OAuthClient()

    def test_init(self):
        """Test OAuthClient initialization"""
        client = OAuthClient()
        assert client._clients == {}

    def test_generate_code_verifier(self, oauth_client):
        """Test PKCE code verifier generation"""
        verifier = oauth_client.generate_code_verifier()

        assert isinstance(verifier, str)
        assert len(verifier) > 0
        # URL-safe base64 encoded, should be 43 characters (32 bytes base64)
        assert len(verifier) == 43

    def test_generate_code_verifier_uniqueness(self, oauth_client):
        """Test that code verifiers are unique"""
        verifier1 = oauth_client.generate_code_verifier()
        verifier2 = oauth_client.generate_code_verifier()

        assert verifier1 != verifier2

    def test_generate_code_challenge(self, oauth_client):
        """Test PKCE code challenge generation (S256)"""
        verifier = "test_verifier_12345678901234567890123"
        challenge = oauth_client.generate_code_challenge(verifier)

        assert isinstance(challenge, str)
        assert len(challenge) > 0
        # S256 challenge should be base64url encoded SHA256 hash (43 chars)
        assert len(challenge) == 43

    @pytest.mark.asyncio
    async def test_fetch_client_credentials_token_success(self, oauth_client):
        mock_authlib_client = AsyncMock()
        mock_authlib_client.fetch_token = AsyncMock(
            return_value={"access_token": "tok", "token_type": "Bearer", "expires_in": 3600, "scope": "a b"}
        )
        mock_authlib_client.aclose = AsyncMock()

        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client", return_value=mock_authlib_client):
            tokens = await oauth_client.fetch_client_credentials_token(
                token_endpoint="https://example.com/oauth/token",
                client_id="client-1",
                client_secret="secret-1",
                scope="a b",
                audience="jarvis-services",
            )

        assert tokens is not None
        assert tokens.access_token == "tok"
        mock_authlib_client.fetch_token.assert_awaited_once()


class TestOAuthClientRegisterClient:
    """Tests for OAuthClient.register_client method (RFC 7591 DCR)"""

    @pytest.fixture
    def oauth_client(self):
        """Create OAuthClient instance"""
        return OAuthClient()

    @pytest.fixture
    def mock_metadata(self):
        """Mock OAuth server metadata"""
        return OAuthMetadata(
            issuer="https://example.com",
            authorization_endpoint="https://example.com/oauth/authorize",
            token_endpoint="https://example.com/oauth/token",
            registration_endpoint="https://example.com/oauth/register",
            scopes_supported=["read", "write"],
            response_types_supported=["code"],
            grant_types_supported=["authorization_code", "refresh_token"],
            token_endpoint_auth_methods_supported=["client_secret_basic", "client_secret_post"],
        )

    @pytest.fixture
    def mock_resource_metadata(self):
        """Mock protected resource metadata"""
        return OAuthProtectedResourceMetadata(
            resource="https://api.example.com",
            authorization_servers=["https://example.com"],
            scopes_supported=["api:read", "api:write"],
        )

    @pytest.mark.asyncio
    async def test_register_client_success(self, oauth_client, mock_metadata):
        """Test successful client registration"""
        # Mock httpx response
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "client_id": "registered_client_123",
            "client_secret": "secret_abc",
            "redirect_uris": ["https://registry.example.com/callback"],
            "scope": "read write",
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "client_secret_basic",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await oauth_client.register_client(
                server_url="https://example.com",
                metadata=mock_metadata,
                redirect_uri="https://registry.example.com/callback",
            )

            # Verify result
            assert isinstance(result, OAuthClientInformation)
            assert result.client_id == "registered_client_123"
            assert result.client_secret == "secret_abc"
            assert result.redirect_uris == ["https://registry.example.com/callback"]
            assert result.scope == "read write"

            # Verify HTTP request was made correctly
            mock_client.post.assert_awaited_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://example.com/oauth/register"
            assert call_args[1]["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_register_client_with_resource_metadata(self, oauth_client, mock_metadata, mock_resource_metadata):
        """Test client registration with resource metadata"""
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "client_id": "registered_client_456",
            "client_secret": "secret_def",
            "redirect_uris": ["https://registry.example.com/callback"],
            "scope": "api:read api:write",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await oauth_client.register_client(
                server_url="https://example.com",
                metadata=mock_metadata,
                resource_metadata=mock_resource_metadata,
                redirect_uri="https://registry.example.com/callback",
            )

            # Verify resource metadata scopes were used
            assert result.scope == "api:read api:write"

    @pytest.mark.asyncio
    async def test_register_client_no_registration_endpoint(self, oauth_client):
        """Test registration fails when no registration_endpoint"""
        metadata_no_dcr = OAuthMetadata(
            issuer="https://example.com",
            authorization_endpoint="https://example.com/oauth/authorize",
            token_endpoint="https://example.com/oauth/token",
            registration_endpoint=None,  # No DCR support
        )

        with pytest.raises(ValueError, match="does not support dynamic client registration"):
            await oauth_client.register_client(
                server_url="https://example.com",
                metadata=metadata_no_dcr,
                redirect_uri="https://registry.example.com/callback",
            )

    @pytest.mark.asyncio
    async def test_register_client_http_error(self, oauth_client, mock_metadata):
        """Test registration fails with HTTP error"""
        mock_response = Mock()
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_response.text = "Invalid client metadata"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            with pytest.raises(httpx.HTTPError, match="Client registration failed"):
                await oauth_client.register_client(
                    server_url="https://example.com",
                    metadata=mock_metadata,
                    redirect_uri="https://registry.example.com/callback",
                )

    @pytest.mark.asyncio
    async def test_register_client_with_preferred_auth_method(self, oauth_client, mock_metadata):
        """Test registration with preferred token endpoint auth method"""
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "client_id": "registered_client_789",
            "client_secret": "secret_ghi",
            "token_endpoint_auth_method": "client_secret_post",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await oauth_client.register_client(
                server_url="https://example.com",
                metadata=mock_metadata,
                redirect_uri="https://registry.example.com/callback",
                token_exchange_method="client_secret_post",
            )

            # Verify registration was successful with correct client_id
            assert result.client_id == "registered_client_789"
            assert result.client_secret == "secret_ghi"

    @pytest.mark.asyncio
    @patch("registry.auth.oauth.oauth_client.settings")
    async def test_register_client_uses_settings_app_name(self, mock_settings, oauth_client, mock_metadata):
        """Test registration includes app name from settings"""
        mock_settings.registry_app_name = "Test Registry"
        mock_response = Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "client_id": "registered_client_999",
            "client_secret": "secret_jkl",
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            await oauth_client.register_client(
                server_url="https://example.com",
                metadata=mock_metadata,
                redirect_uri="https://registry.example.com/callback",
            )

            # Verify client_name was included in request
            call_args = mock_client.post.call_args
            request_json = call_args[1]["json"]
            assert request_json["client_name"] == "Test Registry"


class TestOAuthClientDCRHelperMethods:
    """Tests for OAuthClient DCR helper methods"""

    @pytest.fixture
    def oauth_client(self):
        """Create OAuthClient instance"""
        return OAuthClient()

    def test_negotiate_grant_types_with_refresh_token(self, oauth_client):
        """Test grant type negotiation when refresh_token is supported"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            grant_types_supported=["authorization_code", "refresh_token"],
        )

        result = oauth_client._negotiate_grant_types(metadata)

        assert result == ["authorization_code", "refresh_token"]

    def test_negotiate_grant_types_without_refresh_token(self, oauth_client):
        """Test grant type negotiation when refresh_token is not supported"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            grant_types_supported=["authorization_code"],
        )

        result = oauth_client._negotiate_grant_types(metadata)

        assert result == ["authorization_code"]
        assert "refresh_token" not in result

    def test_negotiate_auth_method_prefers_basic(self, oauth_client):
        """Test auth method negotiation prefers client_secret_basic"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            token_endpoint_auth_methods_supported=["client_secret_basic", "client_secret_post"],
        )

        result = oauth_client._negotiate_auth_method(metadata)

        assert result == "client_secret_basic"

    def test_negotiate_auth_method_fallback_to_post(self, oauth_client):
        """Test auth method negotiation falls back to client_secret_post"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            token_endpoint_auth_methods_supported=["client_secret_post"],
        )

        result = oauth_client._negotiate_auth_method(metadata)

        assert result == "client_secret_post"

    def test_negotiate_auth_method_with_preferred(self, oauth_client):
        """Test auth method negotiation with preferred method"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            token_endpoint_auth_methods_supported=["client_secret_basic", "client_secret_post", "none"],
        )

        result = oauth_client._negotiate_auth_method(metadata, preferred_method="none")

        assert result == "none"

    def test_negotiate_auth_method_default(self, oauth_client):
        """Test auth method negotiation default behavior"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            token_endpoint_auth_methods_supported=[],
        )

        result = oauth_client._negotiate_auth_method(metadata)

        assert result == "client_secret_basic"

    def test_build_scope_from_metadata(self, oauth_client):
        """Test scope building from metadata"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            scopes_supported=["read", "write", "delete"],
        )

        result = oauth_client._build_scope(metadata, resource_metadata=None)

        assert result == "read write delete"

    def test_build_scope_from_resource_metadata(self, oauth_client):
        """Test scope building prioritizes resource metadata"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            scopes_supported=["read", "write"],
        )
        resource_metadata = OAuthProtectedResourceMetadata(
            resource="https://api.example.com",
            scopes_supported=["api:read", "api:write", "api:admin"],
        )

        result = oauth_client._build_scope(metadata, resource_metadata)

        assert result == "api:read api:write api:admin"

    def test_build_scope_none_when_no_scopes(self, oauth_client):
        """Test scope building returns None when no scopes available"""
        metadata = OAuthMetadata(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            scopes_supported=None,
        )

        result = oauth_client._build_scope(metadata, resource_metadata=None)

        assert result is None


class TestOAuthClientAuthorizationUrl:
    """Tests for build_authorization_url method"""

    @pytest.fixture
    def oauth_client(self):
        """Create OAuthClient instance"""
        return OAuthClient()

    @pytest.fixture
    def mock_flow_metadata(self):
        """Mock flow metadata"""
        mock_metadata = Mock(spec=MCPOAuthFlowMetadata)
        mock_metadata.state = "test_flow_id##security_token"
        mock_metadata.metadata = Mock(spec=OAuthMetadata)
        mock_metadata.metadata.authorization_endpoint = "https://example.com/oauth/authorize"
        mock_metadata.metadata.token_endpoint = "https://example.com/oauth/token"
        mock_metadata.metadata.token_endpoint_auth_methods_supported = ["client_secret_basic"]
        mock_metadata.client_info = Mock(spec=OAuthClientInformation)
        mock_metadata.client_info.client_id = "test_client_id"
        mock_metadata.client_info.client_secret = "test_secret"
        mock_metadata.client_info.redirect_uris = ["https://registry.example.com/callback"]
        mock_metadata.client_info.scope = "read write"
        mock_metadata.client_info.additional_params = None
        mock_metadata.resource_metadata = None
        return mock_metadata

    @pytest.mark.asyncio
    async def test_build_authorization_url(self, oauth_client, mock_flow_metadata):
        """Test building OAuth authorization URL with PKCE"""
        code_challenge = "test_challenge_string"
        flow_id = "test_flow_id"

        url = await oauth_client.build_authorization_url(mock_flow_metadata, code_challenge, flow_id)

        # Verify URL contains required parameters
        assert "https://example.com/oauth/authorize" in url
        assert "state=test_flow_id" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "client_id=test_client_id" in url

    @pytest.mark.asyncio
    async def test_build_authorization_url_with_additional_params(self, oauth_client, mock_flow_metadata):
        """Test authorization URL includes additional parameters"""
        mock_flow_metadata.client_info.additional_params = {"prompt": "consent", "access_type": "offline"}
        code_challenge = "test_challenge"
        flow_id = "test_flow_id"

        url = await oauth_client.build_authorization_url(mock_flow_metadata, code_challenge, flow_id)

        assert "prompt=consent" in url
        assert "access_type=offline" in url


class TestOAuthClientTokenExchange:
    """Tests for exchange_code_for_tokens method"""

    @pytest.fixture
    def oauth_client(self):
        """Create OAuthClient instance"""
        return OAuthClient()

    @pytest.fixture
    def mock_flow_metadata(self):
        """Mock flow metadata"""
        mock_metadata = Mock(spec=MCPOAuthFlowMetadata)
        mock_metadata.code_verifier = "test_code_verifier_12345"
        mock_metadata.metadata = Mock(spec=OAuthMetadata)
        mock_metadata.metadata.token_endpoint = "https://example.com/oauth/token"
        mock_metadata.metadata.token_endpoint_auth_methods_supported = ["client_secret_basic"]
        mock_metadata.client_info = Mock(spec=OAuthClientInformation)
        mock_metadata.client_info.client_id = "test_client_id"
        mock_metadata.client_info.client_secret = "test_secret"
        mock_metadata.client_info.redirect_uris = ["https://registry.example.com/callback"]
        mock_metadata.client_info.scope = "read write"
        mock_metadata.client_info.additional_params = None
        mock_metadata.resource_metadata = None
        return mock_metadata

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_success(self, oauth_client, mock_flow_metadata):
        """Test successful token exchange"""
        authorization_code = "test_auth_code"

        # Mock Authlib client
        mock_authlib_client = AsyncMock()
        mock_authlib_client.fetch_token = AsyncMock(
            return_value={
                "access_token": "new_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "new_refresh_token",
                "scope": "read write",
                "expires_at": 1234567890,
            }
        )
        mock_authlib_client.aclose = AsyncMock()

        with patch.object(oauth_client, "_get_client", return_value=mock_authlib_client):
            tokens = await oauth_client.exchange_code_for_tokens(mock_flow_metadata, authorization_code)

            # Verify tokens
            assert tokens is not None
            assert tokens.access_token == "new_access_token"
            assert tokens.refresh_token == "new_refresh_token"
            assert tokens.token_type == "Bearer"
            assert tokens.expires_in == 3600

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_no_metadata(self, oauth_client):
        """Test token exchange fails without metadata"""
        mock_flow = Mock(spec=MCPOAuthFlowMetadata)
        mock_flow.metadata = None
        mock_flow.client_info = None

        tokens = await oauth_client.exchange_code_for_tokens(mock_flow, "test_code")

        assert tokens is None

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens_no_token_endpoint(self, oauth_client, mock_flow_metadata):
        """Test token exchange fails without token endpoint"""
        mock_flow_metadata.metadata.token_endpoint = None

        tokens = await oauth_client.exchange_code_for_tokens(mock_flow_metadata, "test_code")

        assert tokens is None


class TestOAuthClientRefreshTokens:
    """Tests for refresh_tokens method"""

    @pytest.fixture
    def oauth_client(self):
        """Create OAuthClient instance"""
        return OAuthClient()

    @pytest.mark.asyncio
    async def test_refresh_tokens_success(self, oauth_client):
        """Test successful token refresh"""
        oauth_config = {
            "client_id": "test_client_id",
            "client_secret": "test_secret",
            "token_url": "https://example.com/oauth/token",
            "scope": "read write",
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        }
        refresh_token = "valid_refresh_token"

        # Mock Authlib client
        mock_authlib_client = AsyncMock()
        mock_authlib_client.refresh_token = AsyncMock(
            return_value={
                "access_token": "refreshed_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "new_refresh_token",
                "scope": "read write",
                "expires_at": 1234567890,
            }
        )
        mock_authlib_client.aclose = AsyncMock()

        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client", return_value=mock_authlib_client):
            tokens = await oauth_client.refresh_tokens(oauth_config, refresh_token)

            # Verify tokens
            assert tokens is not None
            assert tokens.access_token == "refreshed_access_token"
            assert tokens.refresh_token == "new_refresh_token"

    @pytest.mark.asyncio
    async def test_refresh_tokens_no_token_url(self, oauth_client):
        """Test refresh fails without token URL"""
        oauth_config = {
            "client_id": "test_client_id",
            "client_secret": "test_secret",
        }

        tokens = await oauth_client.refresh_tokens(oauth_config, "refresh_token")

        assert tokens is None

    @pytest.mark.asyncio
    async def test_refresh_tokens_keeps_old_refresh_if_not_rotated(self, oauth_client):
        """Test refresh keeps old refresh_token if server doesn't rotate"""
        oauth_config = {
            "client_id": "test_client_id",
            "client_secret": "test_secret",
            "token_url": "https://example.com/oauth/token",
            "scope": ["read", "write"],  # Test list format
        }
        old_refresh_token = "old_refresh_token"

        # Mock Authlib client - no new refresh_token in response
        mock_authlib_client = AsyncMock()
        mock_authlib_client.refresh_token = AsyncMock(
            return_value={
                "access_token": "refreshed_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                # No refresh_token in response (non-rotating server)
            }
        )
        mock_authlib_client.aclose = AsyncMock()

        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client", return_value=mock_authlib_client):
            tokens = await oauth_client.refresh_tokens(oauth_config, old_refresh_token)

            # Verify old refresh_token is kept
            assert tokens.refresh_token == old_refresh_token


class TestGetClientAuthMethodSelection:
    """Tests for _get_client auth method selection.

    RFC / compatibility requirement: prefer client_secret_post over client_secret_basic
    so that third-party MCP servers that only accept POST body credentials (e.g. HubSpot)
    work out of the box.
    """

    @pytest.fixture
    def oauth_client(self):
        return OAuthClient()

    def _make_flow_metadata(self, auth_methods):
        flow_metadata = Mock(spec=MCPOAuthFlowMetadata)
        flow_metadata.metadata = Mock(spec=OAuthMetadata)
        flow_metadata.metadata.token_endpoint = "https://example.com/oauth/token"
        flow_metadata.metadata.authorization_endpoint = "https://example.com/oauth/authorize"
        flow_metadata.metadata.token_endpoint_auth_methods_supported = auth_methods
        flow_metadata.client_info = Mock(spec=OAuthClientInformation)
        flow_metadata.client_info.client_id = "test_client_id"
        flow_metadata.client_info.client_secret = "test_secret"
        flow_metadata.client_info.redirect_uris = ["https://example.com/callback"]
        flow_metadata.client_info.scope = "read"
        flow_metadata.resource_metadata = None
        return flow_metadata

    def test_prefers_post_when_both_methods_available(self, oauth_client):
        """When both methods are supported, client_secret_post is chosen."""
        flow_metadata = self._make_flow_metadata(["client_secret_post", "client_secret_basic"])
        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client") as mock_cls:
            mock_cls.return_value = Mock()
            oauth_client._get_client(flow_metadata)
        assert mock_cls.call_args[1]["token_endpoint_auth_method"] == "client_secret_post"

    def test_prefers_post_even_when_basic_listed_first(self, oauth_client):
        """client_secret_post wins regardless of list ordering."""
        flow_metadata = self._make_flow_metadata(["client_secret_basic", "client_secret_post"])
        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client") as mock_cls:
            mock_cls.return_value = Mock()
            oauth_client._get_client(flow_metadata)
        assert mock_cls.call_args[1]["token_endpoint_auth_method"] == "client_secret_post"

    def test_uses_basic_when_only_basic_available(self, oauth_client):
        """Falls back to client_secret_basic when post is absent from the supported list."""
        flow_metadata = self._make_flow_metadata(["client_secret_basic"])
        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client") as mock_cls:
            mock_cls.return_value = Mock()
            oauth_client._get_client(flow_metadata)
        assert mock_cls.call_args[1]["token_endpoint_auth_method"] == "client_secret_basic"

    def test_defaults_to_post_when_no_methods_advertised(self, oauth_client):
        """Defaults to client_secret_post when supported list is empty."""
        flow_metadata = self._make_flow_metadata([])
        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client") as mock_cls:
            mock_cls.return_value = Mock()
            oauth_client._get_client(flow_metadata)
        assert mock_cls.call_args[1]["token_endpoint_auth_method"] == "client_secret_post"

    def test_defaults_to_post_when_methods_is_none(self, oauth_client):
        """Defaults to client_secret_post when token_endpoint_auth_methods_supported is None."""
        flow_metadata = self._make_flow_metadata(None)
        with patch("registry.auth.oauth.oauth_client.AsyncOAuth2Client") as mock_cls:
            mock_cls.return_value = Mock()
            oauth_client._get_client(flow_metadata)
        assert mock_cls.call_args[1]["token_endpoint_auth_method"] == "client_secret_post"


class TestBuildAuthorizationUrlResourceIndicator:
    """Tests for RFC 8707 resource indicator parameter in build_authorization_url."""

    @pytest.fixture
    def oauth_client(self):
        return OAuthClient()

    def _make_flow_metadata(self, resource_url=None):
        flow_metadata = Mock(spec=MCPOAuthFlowMetadata)
        flow_metadata.state = "test_state_value"
        flow_metadata.metadata = Mock(spec=OAuthMetadata)
        flow_metadata.metadata.authorization_endpoint = "https://example.com/oauth/authorize"
        flow_metadata.metadata.token_endpoint = "https://example.com/oauth/token"
        flow_metadata.metadata.token_endpoint_auth_methods_supported = ["client_secret_post"]
        flow_metadata.client_info = Mock(spec=OAuthClientInformation)
        flow_metadata.client_info.client_id = "test_client_id"
        flow_metadata.client_info.client_secret = "test_secret"
        flow_metadata.client_info.redirect_uris = ["https://example.com/callback"]
        flow_metadata.client_info.scope = "read"
        flow_metadata.client_info.additional_params = None
        flow_metadata.resource_metadata = (
            OAuthProtectedResourceMetadata(resource=resource_url) if resource_url else None
        )
        return flow_metadata

    @pytest.mark.asyncio
    async def test_includes_resource_param_when_resource_metadata_set(self, oauth_client):
        """Authorization URL must include the resource parameter (RFC 8707)."""
        flow_metadata = self._make_flow_metadata(resource_url="https://mcp.hubspot.com")
        url = await oauth_client.build_authorization_url(flow_metadata, "challenge_abc", "flow_123")
        # URL-encoded or plain — either form is valid
        assert "resource=" in url
        assert "mcp.hubspot.com" in url

    @pytest.mark.asyncio
    async def test_no_resource_param_when_resource_metadata_none(self, oauth_client):
        """Authorization URL must NOT include resource parameter when resource_metadata is None."""
        flow_metadata = self._make_flow_metadata(resource_url=None)
        url = await oauth_client.build_authorization_url(flow_metadata, "challenge_abc", "flow_123")
        assert "resource=" not in url


class TestExchangeCodeForTokensResourceIndicator:
    """Tests for RFC 8707 resource parameter in exchange_code_for_tokens."""

    @pytest.fixture
    def oauth_client(self):
        return OAuthClient()

    def _make_flow_metadata(self, resource_url=None):
        flow_metadata = Mock(spec=MCPOAuthFlowMetadata)
        flow_metadata.code_verifier = "test_verifier_string"
        flow_metadata.metadata = Mock(spec=OAuthMetadata)
        flow_metadata.metadata.token_endpoint = "https://example.com/oauth/token"
        flow_metadata.metadata.token_endpoint_auth_methods_supported = ["client_secret_post"]
        flow_metadata.client_info = Mock(spec=OAuthClientInformation)
        flow_metadata.client_info.client_id = "test_client_id"
        flow_metadata.client_info.client_secret = "test_secret"
        flow_metadata.client_info.redirect_uris = ["https://example.com/callback"]
        flow_metadata.client_info.scope = "read"
        flow_metadata.client_info.additional_params = None
        flow_metadata.resource_metadata = (
            OAuthProtectedResourceMetadata(resource=resource_url) if resource_url else None
        )
        return flow_metadata

    @pytest.mark.asyncio
    async def test_passes_resource_to_fetch_token(self, oauth_client):
        """fetch_token must receive the resource kwarg when resource_metadata is set."""
        flow_metadata = self._make_flow_metadata(resource_url="https://mcp.hubspot.com")
        mock_authlib_client = AsyncMock()
        mock_authlib_client.fetch_token = AsyncMock(
            return_value={"access_token": "tok", "token_type": "Bearer", "expires_in": 3600}
        )
        mock_authlib_client.aclose = AsyncMock()

        with patch.object(oauth_client, "_get_client", return_value=mock_authlib_client):
            await oauth_client.exchange_code_for_tokens(flow_metadata, "test_code")

        call_kwargs = mock_authlib_client.fetch_token.call_args[1]
        assert call_kwargs.get("resource") == "https://mcp.hubspot.com"

    @pytest.mark.asyncio
    async def test_no_resource_when_resource_metadata_none(self, oauth_client):
        """fetch_token must NOT receive a resource kwarg when resource_metadata is None."""
        flow_metadata = self._make_flow_metadata(resource_url=None)
        mock_authlib_client = AsyncMock()
        mock_authlib_client.fetch_token = AsyncMock(
            return_value={"access_token": "tok", "token_type": "Bearer", "expires_in": 3600}
        )
        mock_authlib_client.aclose = AsyncMock()

        with patch.object(oauth_client, "_get_client", return_value=mock_authlib_client):
            await oauth_client.exchange_code_for_tokens(flow_metadata, "test_code")

        call_kwargs = mock_authlib_client.fetch_token.call_args[1]
        assert "resource" not in call_kwargs

    @pytest.mark.asyncio
    async def test_returns_none_on_http_status_error(self, oauth_client):
        """HTTPStatusError from the provider is caught and returns None."""
        flow_metadata = self._make_flow_metadata(resource_url=None)
        mock_request = Mock()
        mock_request.url = "https://example.com/oauth/token"
        mock_request.headers = {"Content-Type": "application/x-www-form-urlencoded"}
        mock_response_obj = Mock()
        mock_response_obj.status_code = 500
        mock_response_obj.text = '{"error":"server_error","error_description":"internal"}'
        http_error = httpx.HTTPStatusError(
            "500 Internal Server Error", request=mock_request, response=mock_response_obj
        )

        mock_authlib_client = AsyncMock()
        mock_authlib_client.fetch_token = AsyncMock(side_effect=http_error)
        mock_authlib_client.aclose = AsyncMock()

        with patch.object(oauth_client, "_get_client", return_value=mock_authlib_client):
            tokens = await oauth_client.exchange_code_for_tokens(flow_metadata, "test_code")

        assert tokens is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
