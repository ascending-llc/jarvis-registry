from unittest.mock import AsyncMock, patch

import pytest

from auth_server.providers.entra import EntraIdProvider
from registry_pkgs.core.jwt_utils import InvalidSignatureError


def _provider() -> EntraIdProvider:
    return EntraIdProvider(
        tenant_id="tenant-id",
        client_id="client-id",
        client_secret="client-secret",
        auth_url="https://login.microsoftonline.com/tenant-id/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token",
        jwks_url="https://login.microsoftonline.com/tenant-id/discovery/v2.0/keys",
        logout_url="https://login.microsoftonline.com/tenant-id/oauth2/v2.0/logout",
        userinfo_url="https://graph.microsoft.com/oidc/userinfo",
    )


@pytest.mark.unit
@pytest.mark.auth
class TestEntraGetUserInfo:
    @pytest.mark.asyncio
    async def test_get_user_info_maps_verified_id_token_claims(self):
        provider = _provider()
        provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})
        provider.get_user_groups = AsyncMock(return_value=["engineering"])

        verified_claims = {
            "preferred_username": "verified@example.com",
            "email": "verified@example.com",
            "name": "Verified User",
            "oid": "verified-oid",
        }

        with (
            patch("auth_server.providers.entra.settings") as mock_settings,
            patch("auth_server.providers.entra.get_token_kid", return_value="kid-1"),
            patch("auth_server.providers.entra.decode_jwt_unverified", return_value={"iss": provider.issuer_v2}),
            patch("auth_server.providers.entra.decode_jwt_with_jwk", return_value=verified_claims) as mock_decode,
        ):
            mock_settings.entra_token_kind = "id"

            user_info = await provider.get_user_info("access-token", id_token="id-token")

        assert user_info["username"] == "verified@example.com"
        assert user_info["email"] == "verified@example.com"
        assert user_info["id"] == "verified-oid"
        assert user_info["groups"] == ["engineering"]
        mock_decode.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_info_rejects_invalid_id_token_signature(self):
        provider = _provider()
        provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})
        provider.get_user_groups = AsyncMock()

        with (
            patch("auth_server.providers.entra.settings") as mock_settings,
            patch("auth_server.providers.entra.get_token_kid", return_value="kid-1"),
            patch("auth_server.providers.entra.decode_jwt_unverified", return_value={"iss": provider.issuer_v2}),
            patch(
                "auth_server.providers.entra.decode_jwt_with_jwk",
                side_effect=InvalidSignatureError("bad signature"),
            ),
        ):
            mock_settings.entra_token_kind = "id"

            with pytest.raises(InvalidSignatureError):
                await provider.get_user_info("access-token", id_token="id-token")

        provider.get_user_groups.assert_not_called()
