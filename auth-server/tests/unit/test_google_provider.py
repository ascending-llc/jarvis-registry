from unittest.mock import AsyncMock, patch

import pytest

from auth_server.providers.google import (
    GoogleDomainNotAllowedError,
    GoogleEmailNotVerifiedError,
    GoogleProvider,
    _group_local_part,
)
from registry_pkgs.core.jwt_utils import InvalidSignatureError
from registry_pkgs.google.cloud_identity_client import GoogleWorkspaceGroupInfo


def _provider(allowed_hd: str = "") -> GoogleProvider:
    return GoogleProvider(
        client_id="client-id",
        client_secret="client-secret",
        cloud_identity_client=AsyncMock(),
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        allowed_hd=allowed_hd,
    )


def _claims(**overrides) -> dict:
    claims = {
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
        "sub": "google-sub-123",
        "hd": "example.com",
        "aud": "client-id",
    }
    claims.update(overrides)
    return claims


@pytest.mark.unit
@pytest.mark.auth
class TestGoogleGetUserInfo:
    @pytest.mark.asyncio
    async def test_returns_group_local_parts_from_cloud_identity(self):
        """Cloud Identity groups are email-addressed, but scopes.yml's group_mappings keys
        are bare role names — get_user_info must strip the domain before returning groups."""
        provider = _provider()
        provider._verify_id_token = AsyncMock(return_value=_claims())
        provider._cloud_identity_client.list_transitive_groups_for_member = AsyncMock(
            return_value=[
                GoogleWorkspaceGroupInfo(email="eng@example.com", display_name="Eng", resource_name="groups/1"),
                GoogleWorkspaceGroupInfo(email="all@example.com", display_name="All", resource_name="groups/2"),
            ]
        )

        info = await provider.get_user_info("access-token", id_token="id-token")

        assert info == {
            "username": "user@example.com",
            "email": "user@example.com",
            "name": "Test User",
            "id": "google-sub-123",
            "groups": ["eng", "all"],
        }

    @pytest.mark.asyncio
    async def test_cloud_identity_failure_returns_empty_groups_and_logs(self):
        """A Cloud Identity outage must degrade to empty groups (not raise), so it never reaches
        oauth_flow's generic userinfo fallback."""
        provider = _provider()
        provider._verify_id_token = AsyncMock(return_value=_claims())
        exc = RuntimeError("cloud identity down")
        provider._cloud_identity_client.list_transitive_groups_for_member = AsyncMock(side_effect=exc)

        with patch("auth_server.providers.google.log_group_resolution_failure") as mock_log:
            info = await provider.get_user_info("access-token", id_token="id-token")

        assert info["email"] == "user@example.com"
        assert info["groups"] == []
        mock_log.assert_called_once_with("google", "user@example.com", exc)

    @pytest.mark.asyncio
    async def test_rejects_unverified_email_before_group_lookup(self):
        provider = _provider()
        provider._verify_id_token = AsyncMock(return_value=_claims(email_verified=False))

        with pytest.raises(GoogleEmailNotVerifiedError):
            await provider.get_user_info("access-token", id_token="id-token")

        provider._cloud_identity_client.list_transitive_groups_for_member.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_mismatched_hd_when_configured(self):
        provider = _provider(allowed_hd="corp.example")
        provider._verify_id_token = AsyncMock(return_value=_claims(hd="attacker.com"))

        with pytest.raises(GoogleDomainNotAllowedError):
            await provider.get_user_info("access-token", id_token="id-token")

        provider._cloud_identity_client.list_transitive_groups_for_member.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_any_hd_when_unset(self):
        provider = _provider(allowed_hd="")
        provider._verify_id_token = AsyncMock(return_value=_claims(hd="anything.com"))
        provider._cloud_identity_client.list_transitive_groups_for_member = AsyncMock(return_value=[])

        info = await provider.get_user_info("access-token", id_token="id-token")

        assert info["email"] == "user@example.com"
        assert info["groups"] == []

    @pytest.mark.asyncio
    async def test_requires_id_token(self):
        provider = _provider()

        with pytest.raises(ValueError, match="id_token"):
            await provider.get_user_info("access-token", id_token=None)

    @pytest.mark.asyncio
    async def test_rejects_invalid_signature(self):
        provider = _provider()
        provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})

        with (
            patch("auth_server.providers.google.get_token_kid", return_value="kid-1"),
            patch(
                "auth_server.providers.google.decode_jwt_with_jwk",
                side_effect=InvalidSignatureError("bad signature"),
            ),
        ):
            with pytest.raises(InvalidSignatureError):
                await provider.get_user_info("access-token", id_token="id-token")


@pytest.mark.unit
@pytest.mark.auth
class TestGoogleVerifyIdToken:
    @pytest.mark.asyncio
    async def test_verifies_against_accepted_issuers_and_audience(self):
        provider = _provider()
        provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})

        with (
            patch("auth_server.providers.google.get_token_kid", return_value="kid-1"),
            patch("auth_server.providers.google.decode_jwt_with_jwk", return_value=_claims()) as mock_decode,
        ):
            claims = await provider._verify_id_token("id-token")

        assert claims["email"] == "user@example.com"
        _, kwargs = mock_decode.call_args
        assert kwargs["issuer"] == ["https://accounts.google.com", "accounts.google.com"]
        assert kwargs["audience"] == "client-id"


@pytest.mark.unit
@pytest.mark.auth
class TestGoogleJwksCaching:
    @pytest.mark.asyncio
    async def test_caches_jwks_within_ttl(self):
        provider = _provider()
        jwks = {"keys": [{"kid": "kid-1"}]}

        with patch("auth_server.providers.google.httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__aenter__.return_value
            mock_response = AsyncMock()
            mock_response.json = lambda: jwks
            mock_response.raise_for_status = lambda: None
            mock_client.get = AsyncMock(return_value=mock_response)

            first = await provider.get_jwks()
            second = await provider.get_jwks()

        assert first == jwks
        assert second == jwks
        mock_client.get.assert_awaited_once()  # second call served from cache


@pytest.mark.unit
@pytest.mark.auth
class TestGoogleMisc:
    def test_auth_url_includes_hd_when_configured(self):
        url = _provider(allowed_hd="corp.example").get_auth_url("https://cb", "state-1")
        assert "hd=corp.example" in url

    def test_auth_url_omits_hd_when_unset(self):
        url = _provider().get_auth_url("https://cb", "state-1")
        assert "hd=" not in url

    def test_logout_url_returns_redirect_unchanged(self):
        assert _provider().get_logout_url("https://back") == "https://back"

    @pytest.mark.asyncio
    async def test_m2m_not_supported(self):
        provider = _provider()
        with pytest.raises(NotImplementedError):
            await provider.get_m2m_token()
        with pytest.raises(NotImplementedError):
            await provider.validate_m2m_token("token")


@pytest.mark.unit
@pytest.mark.auth
class TestGroupLocalPart:
    def test_strips_domain(self):
        assert _group_local_part("jarvis-registry-admin@example.com") == "jarvis-registry-admin"

    def test_strips_only_first_at(self):
        assert _group_local_part("a@b@example.com") == "a"

    def test_leaves_bare_string_unchanged(self):
        assert _group_local_part("jarvis-registry-admin") == "jarvis-registry-admin"
