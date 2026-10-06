"""Tests for generated-token purpose and consent policy."""

from typing import cast

import pytest
from fastapi import HTTPException

from registry.constants import (
    GENERATED_TOKEN_EMPTY_REQUEST_DETAIL,
    GENERATED_TOKEN_NO_GRANTABLE_SCOPES_DETAIL,
    GENERATED_TOKEN_NO_USER_SCOPES_DETAIL,
    GENERATED_TOKEN_SCOPES_OUTSIDE_CEILING_DETAIL,
)
from registry.core.config import settings
from registry.schemas.enums import TokenPurpose
from registry.services.generated_token_policy import (
    INTERACTIVE_CLIENT_ID,
    get_user_token_scopes,
    is_consent_exempt,
    resolve_generated_token_client_id,
    resolve_generated_token_scopes,
)
from registry_pkgs.core.config import JwtTokenConfig

HEADLESS_CLIENT_ID = "test-headless-agent"
POWER_USER_SCOPES = get_user_token_scopes(["jarvis-registry-power-user"])


@pytest.mark.parametrize(
    ("token_purpose", "expected_client_id"),
    [
        (TokenPurpose.INTERACTIVE, INTERACTIVE_CLIENT_ID),
        (TokenPurpose.AGENT, HEADLESS_CLIENT_ID),
    ],
)
def test_resolve_generated_token_client_id(
    token_purpose: TokenPurpose,
    expected_client_id: str,
) -> None:
    assert resolve_generated_token_client_id(token_purpose, HEADLESS_CLIENT_ID) == expected_client_id


def test_resolve_generated_token_client_id_rejects_unsupported_purpose() -> None:
    with pytest.raises(ValueError, match="Unsupported token purpose"):
        resolve_generated_token_client_id(cast(TokenPurpose, "unsupported"), HEADLESS_CLIENT_ID)


@pytest.mark.parametrize(
    ("client_id", "expected"),
    [
        (HEADLESS_CLIENT_ID, True),
        (INTERACTIVE_CLIENT_ID, False),
        ("other-client", False),
    ],
)
def test_is_consent_exempt(client_id: str, expected: bool) -> None:
    assert is_consent_exempt(client_id, HEADLESS_CLIENT_ID) is expected


@pytest.fixture
def jwt_config() -> JwtTokenConfig:
    return settings.jwt_token_config


def test_get_user_token_scopes_includes_proxy_ops_from_groups() -> None:
    scopes = get_user_token_scopes(["jarvis-registry-power-user"])

    assert "mcp-proxy-ops" in scopes
    assert "a2a-proxy-ops" in scopes


def test_get_user_token_scopes_unmapped_groups_is_empty() -> None:
    assert get_user_token_scopes(["unmapped-group"]) == []


def test_resolve_scopes_rejects_explicit_empty_request(jwt_config: JwtTokenConfig) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_generated_token_scopes([], POWER_USER_SCOPES, TokenPurpose.AGENT, jwt_config)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_EMPTY_REQUEST_DETAIL


@pytest.mark.parametrize("token_purpose", [TokenPurpose.INTERACTIVE, TokenPurpose.AGENT])
def test_resolve_scopes_rejects_scopes_beyond_user_permissions(
    token_purpose: TokenPurpose,
    jwt_config: JwtTokenConfig,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_generated_token_scopes(["system-ops", "mcp-proxy-ops"], POWER_USER_SCOPES, token_purpose, jwt_config)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Requested scopes exceed user permissions. Invalid scopes: ['system-ops']"


def test_resolve_scopes_user_permission_check_runs_before_ceiling(jwt_config: JwtTokenConfig) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_generated_token_scopes(
            ["system-ops", "servers-read"], POWER_USER_SCOPES, TokenPurpose.AGENT, jwt_config
        )

    assert exc_info.value.status_code == 403


def test_resolve_scopes_rejects_explicit_scopes_outside_ceiling(jwt_config: JwtTokenConfig) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_generated_token_scopes(
            ["servers-write", "mcp-proxy-ops", "servers-read"],
            POWER_USER_SCOPES,
            TokenPurpose.AGENT,
            jwt_config,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_SCOPES_OUTSIDE_CEILING_DETAIL.format(
        purpose="Non-interactive agent",
        rejected="servers-read, servers-write",
        allowed="a2a-proxy-ops, mcp-proxy-ops",
    )


@pytest.mark.parametrize(
    ("requested", "token_purpose", "expected"),
    [
        (["a2a-proxy-ops", "mcp-proxy-ops"], TokenPurpose.AGENT, ["a2a-proxy-ops", "mcp-proxy-ops"]),
        (["mcp-proxy-ops", "mcp-proxy-ops"], TokenPurpose.AGENT, ["mcp-proxy-ops"]),
        (
            ["skills-read", "servers-read", "skills-read", "mcp-proxy-ops"],
            TokenPurpose.INTERACTIVE,
            ["skills-read", "servers-read", "mcp-proxy-ops"],
        ),
    ],
)
def test_resolve_scopes_returns_explicit_request_deduplicated_in_order(
    requested: list[str],
    token_purpose: TokenPurpose,
    expected: list[str],
    jwt_config: JwtTokenConfig,
) -> None:
    assert resolve_generated_token_scopes(requested, POWER_USER_SCOPES, token_purpose, jwt_config) == expected


@pytest.mark.parametrize("token_purpose", [TokenPurpose.INTERACTIVE, TokenPurpose.AGENT])
def test_resolve_scopes_current_mode_without_user_scopes(
    token_purpose: TokenPurpose,
    jwt_config: JwtTokenConfig,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_generated_token_scopes(None, [], token_purpose, jwt_config)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_NO_USER_SCOPES_DETAIL


def test_resolve_scopes_current_mode_no_grantable_scopes(jwt_config: JwtTokenConfig) -> None:
    read_only_scopes = get_user_token_scopes(["jarvis-registry-read-only"])

    with pytest.raises(HTTPException) as exc_info:
        resolve_generated_token_scopes(None, read_only_scopes, TokenPurpose.AGENT, jwt_config)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_NO_GRANTABLE_SCOPES_DETAIL.format(
        purpose="Non-interactive agent",
        allowed="a2a-proxy-ops, mcp-proxy-ops",
    )


def test_resolve_scopes_current_mode_narrows_agent_token_silently(jwt_config: JwtTokenConfig) -> None:
    assert resolve_generated_token_scopes(None, POWER_USER_SCOPES, TokenPurpose.AGENT, jwt_config) == [
        "mcp-proxy-ops",
        "a2a-proxy-ops",
    ]


def test_resolve_scopes_current_mode_keeps_available_order_for_interactive(jwt_config: JwtTokenConfig) -> None:
    assert (
        resolve_generated_token_scopes(None, POWER_USER_SCOPES, TokenPurpose.INTERACTIVE, jwt_config)
        == POWER_USER_SCOPES
    )


def test_resolve_scopes_messages_never_use_article_before_label(jwt_config: JwtTokenConfig) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_generated_token_scopes(["not-a-scope"], ["not-a-scope"], TokenPurpose.INTERACTIVE, jwt_config)

    assert exc_info.value.status_code == 400
    assert '"Interactive"' in exc_info.value.detail
    assert "a Interactive" not in exc_info.value.detail
