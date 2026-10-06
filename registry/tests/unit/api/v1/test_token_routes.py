"""Tests for user-vended managed-agent token generation."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from registry.api.v1.token_routes import generate_user_token
from registry.constants import (
    GENERATED_TOKEN_EMPTY_REQUEST_DETAIL,
    GENERATED_TOKEN_NO_GRANTABLE_SCOPES_DETAIL,
    GENERATED_TOKEN_NO_USER_SCOPES_DETAIL,
    GENERATED_TOKEN_SCOPES_OUTSIDE_CEILING_DETAIL,
)
from registry.core.config import settings
from registry.schemas.common_api_schemas import TokenGenerateRequest, TokenGenerateResponse
from registry.schemas.enums import TokenPurpose
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.core.jwt_tokens import MintedManagedAgentToken, verify_managed_agent_token
from registry_pkgs.core.scopes import map_groups_to_scopes

POWER_USER_GROUPS = ["jarvis-registry-power-user"]
POWER_USER_TOKEN_SCOPES = map_groups_to_scopes(POWER_USER_GROUPS)

# Token generation checks scopes against the user's group mappings; the session `scopes` (which
# never contain proxy-ops) are deliberately ignored.
USER_CONTEXT = {
    "username": "alice",
    "user_id": "507f1f77bcf86cd799439011",
    "scopes": ["servers-read"],
    "groups": POWER_USER_GROUPS,
}
READ_ONLY_USER_CONTEXT = {**USER_CONTEXT, "groups": ["jarvis-registry-read-only"]}
UNMAPPED_USER_CONTEXT = {**USER_CONTEXT, "groups": ["unmapped-group"]}


def _scope_claim(result: TokenGenerateResponse) -> str:
    claims = verify_managed_agent_token(settings.jwt_token_config, result.tokenData.accessToken)
    assert claims["scope"] == result.tokenData.scope
    return claims["scope"]


@pytest.mark.parametrize(
    ("request_data", "expected_client_id"),
    [
        (TokenGenerateRequest(expiresInHours=8), INTERACTIVE_CLIENT_ID),
        (
            TokenGenerateRequest(expiresInHours=8, tokenPurpose=TokenPurpose.INTERACTIVE),
            INTERACTIVE_CLIENT_ID,
        ),
        (
            TokenGenerateRequest(expiresInHours=8, tokenPurpose=TokenPurpose.AGENT),
            settings.headless_agent_client_id,
        ),
    ],
)
async def test_generate_user_token_selects_client_id_from_purpose(
    request_data: TokenGenerateRequest,
    expected_client_id: str,
) -> None:
    with patch(
        "registry.api.v1.token_routes.mint_managed_agent_token_with_scope",
        return_value=MintedManagedAgentToken(token="signed-token", scope="mcp-proxy-ops"),
    ) as mint_token:
        result = await generate_user_token(request_data, USER_CONTEXT)

    assert result.tokenData.accessToken == "signed-token"
    assert result.tokenData.scope == "mcp-proxy-ops"
    assert result.userScopes == POWER_USER_TOKEN_SCOPES
    assert result.requestedScopes == POWER_USER_TOKEN_SCOPES
    mint_token.assert_called_once()
    call_kwargs = mint_token.call_args.kwargs
    assert call_kwargs["client_id"] == expected_client_id
    assert call_kwargs["expires_in_seconds"] == 8 * 3600
    assert call_kwargs["extra_claims"]["user_id"] == USER_CONTEXT["user_id"]
    assert "scope" not in call_kwargs["extra_claims"]
    assert call_kwargs["extra_claims"]["groups"] == USER_CONTEXT["groups"]


async def test_generate_agent_token_with_current_scopes_grants_proxy_ops() -> None:
    request_data = TokenGenerateRequest(expiresInHours=8, tokenPurpose=TokenPurpose.AGENT)

    result = await generate_user_token(request_data, USER_CONTEXT)

    assert _scope_claim(result) == "mcp-proxy-ops a2a-proxy-ops"
    assert result.userScopes == POWER_USER_TOKEN_SCOPES
    assert result.requestedScopes == POWER_USER_TOKEN_SCOPES


async def test_generate_interactive_token_with_current_scopes_includes_proxy_ops() -> None:
    request_data = TokenGenerateRequest(expiresInHours=8, tokenPurpose=TokenPurpose.INTERACTIVE)

    result = await generate_user_token(request_data, USER_CONTEXT)

    scopes = _scope_claim(result).split()
    assert "mcp-proxy-ops" in scopes
    assert "a2a-proxy-ops" in scopes
    assert scopes == POWER_USER_TOKEN_SCOPES


async def test_generate_agent_token_without_grantable_scopes_is_rejected() -> None:
    request_data = TokenGenerateRequest(expiresInHours=8, tokenPurpose=TokenPurpose.AGENT)

    with (
        patch("registry.api.v1.token_routes.mint_managed_agent_token_with_scope") as mint_token,
        pytest.raises(HTTPException) as exc_info,
    ):
        await generate_user_token(request_data, READ_ONLY_USER_CONTEXT)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_NO_GRANTABLE_SCOPES_DETAIL.format(
        purpose="Non-interactive agent",
        allowed="a2a-proxy-ops, mcp-proxy-ops",
    )
    mint_token.assert_not_called()


async def test_generate_agent_token_rejects_custom_scopes_outside_ceiling() -> None:
    request_data = TokenGenerateRequest(
        expiresInHours=8,
        requestedScopes=["servers-read", "mcp-proxy-ops"],
        tokenPurpose=TokenPurpose.AGENT,
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_user_token(request_data, USER_CONTEXT)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_SCOPES_OUTSIDE_CEILING_DETAIL.format(
        purpose="Non-interactive agent",
        rejected="servers-read",
        allowed="a2a-proxy-ops, mcp-proxy-ops",
    )


async def test_generate_token_rejects_custom_scopes_beyond_user_permissions() -> None:
    request_data = TokenGenerateRequest(expiresInHours=8, requestedScopes=["system-ops"])

    with pytest.raises(HTTPException) as exc_info:
        await generate_user_token(request_data, USER_CONTEXT)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail.startswith("Requested scopes exceed user permissions")


async def test_generate_token_rejects_empty_custom_scopes() -> None:
    request_data = TokenGenerateRequest(expiresInHours=8, requestedScopes=[])

    with pytest.raises(HTTPException) as exc_info:
        await generate_user_token(request_data, USER_CONTEXT)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_EMPTY_REQUEST_DETAIL


async def test_generate_token_deduplicates_custom_scopes() -> None:
    request_data = TokenGenerateRequest(
        expiresInHours=8,
        requestedScopes=["mcp-proxy-ops", "mcp-proxy-ops"],
        tokenPurpose=TokenPurpose.AGENT,
    )

    result = await generate_user_token(request_data, USER_CONTEXT)

    assert _scope_claim(result) == "mcp-proxy-ops"
    assert result.requestedScopes == ["mcp-proxy-ops", "mcp-proxy-ops"]


@pytest.mark.parametrize("token_purpose", [TokenPurpose.INTERACTIVE, TokenPurpose.AGENT])
async def test_generate_token_for_user_without_mapped_groups_is_rejected(token_purpose: TokenPurpose) -> None:
    request_data = TokenGenerateRequest(expiresInHours=8, tokenPurpose=token_purpose)

    with pytest.raises(HTTPException) as exc_info:
        await generate_user_token(request_data, UNMAPPED_USER_CONTEXT)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == GENERATED_TOKEN_NO_USER_SCOPES_DETAIL


def test_token_generate_request_rejects_unknown_purpose() -> None:
    with pytest.raises(ValidationError):
        TokenGenerateRequest(expiresInHours=8, tokenPurpose="unsupported")
