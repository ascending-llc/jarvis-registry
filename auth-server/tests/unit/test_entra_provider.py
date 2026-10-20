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


def _claims(**overrides: object) -> dict:
    claims = {
        "iss": "https://login.microsoftonline.com/tenant-id/v2.0",
        "sub": "verified-sub",
        "oid": "verified-oid",
        "preferred_username": "verified@example.com",
        "email": "verified@example.com",
        "name": "Verified User",
        "xms_edov": True,
    }
    claims.update(overrides)
    return claims


@pytest.mark.unit
@pytest.mark.auth
class TestEntraGetUserInfo:
    @pytest.mark.asyncio
    async def test_get_user_info_maps_verified_id_token_claims(self):
        provider = _provider()
        provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})
        provider.get_scope_groups = AsyncMock(return_value=["engineering"])

        verified_claims = _claims()

        with (
            patch("auth_server.providers.entra.get_token_kid", return_value="kid-1"),
            patch("auth_server.providers.entra.decode_jwt_unverified", return_value={"iss": provider.issuer_v2}),
            patch("auth_server.providers.entra.decode_jwt_with_jwk", return_value=verified_claims) as mock_decode,
        ):
            user_info = await provider.get_user_info("access-token", id_token="id-token")

        assert user_info == {
            "username": "verified@example.com",
            "email": "verified@example.com",
            "name": "Verified User",
            "id": "verified-oid",
            "groups": ["engineering"],
            "issuer": provider.issuer_v2,
            "subject": "verified-sub",
            "object_id": "verified-oid",
            "email_trusted": True,
        }
        mock_decode.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_info_rejects_invalid_id_token_signature(self):
        provider = _provider()
        provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})
        provider.get_scope_groups = AsyncMock()

        with (
            patch("auth_server.providers.entra.get_token_kid", return_value="kid-1"),
            patch("auth_server.providers.entra.decode_jwt_unverified", return_value={"iss": provider.issuer_v2}),
            patch(
                "auth_server.providers.entra.decode_jwt_with_jwk",
                side_effect=InvalidSignatureError("bad signature"),
            ),
        ):
            with pytest.raises(InvalidSignatureError):
                await provider.get_user_info("access-token", id_token="id-token")

        provider.get_scope_groups.assert_not_called()

    @pytest.mark.parametrize("id_token", [None, ""])
    async def test_requires_id_token_before_any_provider_request(self, id_token: str | None) -> None:
        provider = _provider()
        provider._verify_user_info_token = AsyncMock()
        provider.get_scope_groups = AsyncMock()
        with pytest.raises(ValueError, match="Entra login requires an id_token"):
            await provider.get_user_info("access-token", id_token=id_token)
        provider._verify_user_info_token.assert_not_awaited()
        provider.get_scope_groups.assert_not_awaited()

    @pytest.mark.parametrize("field", ["iss", "sub", "oid"])
    async def test_missing_identity_claim_rejects_login_before_group_lookup(self, field: str) -> None:
        provider = _provider()
        claims = _claims()
        del claims[field]
        provider._verify_user_info_token = AsyncMock(return_value=claims)
        provider.get_scope_groups = AsyncMock()
        with pytest.raises(ValueError, match=field):
            await provider.get_user_info("access-token", id_token="id-token")
        provider.get_scope_groups.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.auth
class TestEntraExtractIdentity:
    @pytest.mark.parametrize("trusted", [True, False, None, "true", "false", 1, 0])
    def test_only_boolean_xms_edov_is_trusted(self, trusted: object) -> None:
        identity = _provider()._extract_user_info_from_token(_claims(xms_edov=trusted))
        assert identity["email_trusted"] is (trusted is True)

    def test_missing_xms_edov_is_untrusted(self) -> None:
        claims = _claims()
        del claims["xms_edov"]
        assert _provider()._extract_user_info_from_token(claims)["email_trusted"] is False

    def test_normalizes_email_without_changing_subject_or_display_username(self) -> None:
        identity = _provider()._extract_user_info_from_token(
            _claims(email="  Verified@EXAMPLE.COM  ", preferred_username="Different@EXAMPLE.COM")
        )
        assert identity["email"] == "verified@example.com"
        assert identity["username"] == "Different@EXAMPLE.COM"
        assert identity["subject"] == "verified-sub"
        assert identity["object_id"] == "verified-oid"

    @pytest.mark.parametrize("email", [None, "", "  "])
    def test_never_uses_upn_as_email(self, email: str | None) -> None:
        identity = _provider()._extract_user_info_from_token(
            _claims(email=email, preferred_username=None, upn="display@example.com")
        )
        assert identity["email"] is None
        assert identity["username"] == "display@example.com"

    def test_missing_email_is_allowed_for_stable_identity_lookup(self) -> None:
        claims = _claims()
        del claims["email"]
        identity = _provider()._extract_user_info_from_token(claims)
        assert identity["email"] is None
        assert identity["subject"] == "verified-sub"

    @pytest.mark.parametrize("field", ["iss", "sub", "oid"])
    @pytest.mark.parametrize("value", [None, "", "  ", 123, True])
    def test_rejects_invalid_identity_claim(self, field: str, value: object) -> None:
        with pytest.raises(ValueError, match=field):
            _provider()._extract_user_info_from_token(_claims(**{field: value}))

    def test_rejects_non_string_email(self) -> None:
        with pytest.raises(ValueError, match="email must be a string"):
            _provider()._extract_user_info_from_token(_claims(email=["user@example.com"]))


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

        verified_claims = _claims()

        with (
            patch("auth_server.providers.entra.get_token_kid", return_value="kid-1"),
            patch("auth_server.providers.entra.decode_jwt_unverified", return_value={"iss": provider.issuer_v2}),
            patch("auth_server.providers.entra.decode_jwt_with_jwk", return_value=verified_claims),
        ):
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

    async def test_timeout_logs_full_exception_details(self, caplog: pytest.LogCaptureFixture) -> None:
        provider = _provider()
        with patch("auth_server.providers.entra.httpx.AsyncClient") as client_class:
            client_class.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.ReadTimeout("graph timed out")
            )
            assert await provider.get_scope_groups("access-token") == []
        record = next(r for r in caplog.records if "Group resolution failed for provider=entra" in r.getMessage())
        assert "graph timed out" in record.getMessage()
        assert record.exc_info is not None
        assert record.exc_info[0] is httpx.ReadTimeout
