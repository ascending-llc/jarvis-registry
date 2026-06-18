"""Unit tests for downstream MCP confirmation tokens (mint + verify).

The security-critical property is the ``(user_id, server_path)`` binding encoded in ``iss``:
a token minted for one user/server must be rejected on any other user/server route.
"""

from registry.auth.downstream_token import (
    DOWNSTREAM_MCP_TOKEN_TTL_SECONDS,
    mint_downstream_mcp_token,
    verify_downstream_mcp_token,
)
from registry.core.config import settings
from registry_pkgs.core.downstream_oauth import downstream_mcp_issuer
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

USER_A = "507f1f77bcf86cd799439011"
USER_B = "507f1f77bcf86cd799439012"


def _mint(user_id: str = USER_A, server_path: str = "github") -> str:
    return mint_downstream_mcp_token(settings.jwt_token_config, user_id=user_id, server_path=server_path)


def _path(user_id: str = USER_A, server_path: str = "github") -> str:
    return f"/proxy/server/{user_id}/{server_path}"


def test_round_trip_returns_user_id():
    token = _mint()
    assert verify_downstream_mcp_token(token, _path(), settings.jwt_public_key) == USER_A


def test_round_trip_with_sub_path():
    # The token binds to whatever server_path string was used; a sub-path round-trips as long as
    # mint and verify see the same string.
    token = _mint(server_path="github/mcp")
    assert verify_downstream_mcp_token(token, _path(server_path="github/mcp"), settings.jwt_public_key) == USER_A


def test_rejects_cross_user():
    # Token minted for user A, presented on user B's route → rejected (iss mismatch).
    token = _mint(user_id=USER_A)
    assert verify_downstream_mcp_token(token, _path(user_id=USER_B), settings.jwt_public_key) is None


def test_rejects_cross_server():
    # Token minted for github, presented on slack's route → rejected (iss mismatch).
    token = _mint(server_path="github")
    assert verify_downstream_mcp_token(token, _path(server_path="slack"), settings.jwt_public_key) is None


def test_rejects_sub_path_mismatch():
    # Bound to bare "github" but presented on "github/mcp" → rejected.
    token = _mint(server_path="github")
    assert verify_downstream_mcp_token(token, _path(server_path="github/mcp"), settings.jwt_public_key) is None


def test_rejects_non_direct_connect_path():
    token = _mint()
    assert verify_downstream_mcp_token(token, "/proxy/mcpgw/mcp", settings.jwt_public_key) is None


def test_rejects_managed_agent_token_wrong_class():
    # A valid managed-agent token (different token_class, different iss) must not pass.
    token = mint_managed_agent_token(
        settings.jwt_token_config,
        subject="alice",
        client_id="mcp-client-abc",
        expires_in_seconds=3600,
        extra_claims={"scope": "mcp-proxy-ops"},
    )
    assert verify_downstream_mcp_token(token, _path(), settings.jwt_public_key) is None


def test_rejects_garbage_token():
    assert verify_downstream_mcp_token("not.a.jwt", _path(), settings.jwt_public_key) is None


def test_rejects_expired_token(monkeypatch):
    # Mint with a negative TTL so the token is already expired, then verify rejects it.
    monkeypatch.setattr("registry.auth.downstream_token.DOWNSTREAM_MCP_TOKEN_TTL_SECONDS", -3600)
    token = _mint()
    assert verify_downstream_mcp_token(token, _path(), settings.jwt_public_key) is None


def test_issuer_format_matches_helper():
    # The token's iss must be exactly what the shared builder produces (the AS-metadata contract).
    expected = downstream_mcp_issuer(settings.jwt_issuer, USER_A, "github")
    assert expected == f"{settings.jwt_issuer}/proxy/server/oauth/{USER_A}/github"
    # And a token minted for that pair verifies only on the matching path.
    token = _mint()
    assert verify_downstream_mcp_token(token, _path(), settings.jwt_public_key) == USER_A


def test_ttl_constant_is_short():
    # Guards against accidentally widening the confirmation token's lifetime.
    assert DOWNSTREAM_MCP_TOKEN_TTL_SECONDS == 300
