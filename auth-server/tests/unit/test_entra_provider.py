from unittest.mock import AsyncMock, patch

import httpx
import pytest

from auth_server.core.config import settings
from auth_server.providers.entra import EntraIdProvider
from registry_pkgs.core.jwt_utils import InvalidSignatureError
from registry_pkgs.core.scope_groups import entra_scope_group_ids
from registry_pkgs.core.scopes import map_groups_to_scopes


def _provider() -> EntraIdProvider:
    return EntraIdProvider(
        scope_group_ids=entra_scope_group_ids(settings),
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
        provider.get_scope_groups = AsyncMock(return_value=["engineering"])

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
        provider.get_scope_groups = AsyncMock()

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

        provider.get_scope_groups.assert_not_called()


@pytest.mark.unit
@pytest.mark.auth
class TestEntraGetScopeGroups:
    @pytest.mark.asyncio
    async def test_get_scope_groups_failure_returns_empty_and_logs(self):
        provider = _provider()

        with (
            patch("auth_server.providers.entra.httpx.AsyncClient", side_effect=RuntimeError("graph down")),
            patch("auth_server.providers.entra.log_group_resolution_failure") as mock_log,
        ):
            groups = await provider.get_scope_groups("access-token")

        assert groups == []
        mock_log.assert_called_once()
        args = mock_log.call_args.args
        assert args[0] == "entra"
        assert isinstance(args[1], RuntimeError)

    @pytest.mark.asyncio
    async def test_get_user_info_resolves_scope_groups_with_graph_token(self):
        provider = _provider()
        provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})
        provider.get_scope_groups = AsyncMock(return_value=[])

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
            patch("auth_server.providers.entra.decode_jwt_with_jwk", return_value=verified_claims),
        ):
            mock_settings.entra_token_kind = "id"
            await provider.get_user_info("access-token", id_token="id-token")

        provider.get_scope_groups.assert_awaited_once_with("access-token")

    @pytest.mark.parametrize("is_member", [True, False])
    async def test_group_identity_controls_admin_scopes(self, is_member: bool) -> None:
        """A same-named group grants nothing unless Graph confirms the configured ID."""
        provider = _provider()
        admin_id = next(iter(provider.scope_group_ids))
        response = httpx.Response(
            200,
            json={"value": [admin_id.upper()] if is_member else []},
            request=httpx.Request("POST", "https://graph.microsoft.com/v1.0/me/checkMemberGroups"),
        )
        with patch("auth_server.providers.entra.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.post = AsyncMock(return_value=response)
            groups = await provider.get_scope_groups("access-token")
            client.post.assert_awaited_once_with(
                "https://graph.microsoft.com/v1.0/me/checkMemberGroups",
                headers={"Authorization": "Bearer access-token"},
                json={"groupIds": list(provider.scope_group_ids)},
                timeout=10,
            )
        assert len(provider.scope_group_ids) == 4
        assert groups == (["jarvis-registry-admin"] if is_member else [])
        assert ("servers-write" in map_groups_to_scopes(groups)) is is_member

    @pytest.mark.parametrize(
        "payload",
        [{}, {"value": None}, {"value": "jarvis-registry-admin"}, {"value": [123]}, {"value": [{"id": "bad"}]}],
    )
    async def test_invalid_graph_response_never_grants_groups(self, payload: dict) -> None:
        provider = _provider()
        response = httpx.Response(200, json=payload, request=httpx.Request("POST", "https://graph.microsoft.com"))
        with (
            patch("auth_server.providers.entra.httpx.AsyncClient") as client_class,
            patch("auth_server.providers.entra.log_group_resolution_failure") as log_failure,
        ):
            client_class.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
            assert await provider.get_scope_groups("access-token") == []
        log_failure.assert_called_once()

    @pytest.mark.parametrize("status", [403, 429, 500])
    async def test_http_error_returns_empty_groups_and_logs(self, status: int) -> None:
        provider = _provider()
        response = httpx.Response(status, request=httpx.Request("POST", "https://graph.microsoft.com"))
        with (
            patch("auth_server.providers.entra.httpx.AsyncClient") as client_class,
            patch("auth_server.providers.entra.log_group_resolution_failure") as log_failure,
        ):
            client_class.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
            assert await provider.get_scope_groups("access-token") == []
        assert isinstance(log_failure.call_args.args[1], httpx.HTTPStatusError)

    async def test_timeout_logs_no_token_or_user_information(self, caplog: pytest.LogCaptureFixture) -> None:
        provider = _provider()
        with patch("auth_server.providers.entra.httpx.AsyncClient") as client_class:
            client_class.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ReadTimeout("user@example.com secret-access-token")
            )
            assert await provider.get_scope_groups("secret-access-token") == []
        assert "Group resolution failed for provider=entra error_type=ReadTimeout" in caplog.text
        assert "user@example.com" not in caplog.text
        assert "secret-access-token" not in caplog.text
