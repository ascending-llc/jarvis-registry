"""Tests for user-vended managed-agent token generation."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from registry.api.v1.token_routes import generate_user_token
from registry.core.config import settings
from registry.schemas.common_api_schemas import TokenGenerateRequest
from registry.schemas.enums import TokenPurpose
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.core.jwt_tokens import MintedManagedAgentToken, verify_managed_agent_token

USER_CONTEXT = {
    "username": "alice",
    "user_id": "507f1f77bcf86cd799439011",
    "scopes": ["mcp:read", "mcp:execute"],
    "groups": ["developers"],
}

CEILING_TEST_SCOPES = ["servers-write", "mcp-proxy-ops", "a2a-proxy-ops"]
CEILING_TEST_USER_CONTEXT = {
    **USER_CONTEXT,
    "scopes": CEILING_TEST_SCOPES,
}


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
        return_value=MintedManagedAgentToken(token="signed-token", scope=" ".join(USER_CONTEXT["scopes"])),
    ) as mint_token:
        result = await generate_user_token(request_data, USER_CONTEXT)

    assert result.tokenData.accessToken == "signed-token"
    assert result.tokenData.scope == " ".join(USER_CONTEXT["scopes"])
    assert result.requestedScopes == USER_CONTEXT["scopes"]
    mint_token.assert_called_once()
    call_kwargs = mint_token.call_args.kwargs
    assert call_kwargs["client_id"] == expected_client_id
    assert call_kwargs["expires_in_seconds"] == 8 * 3600
    assert call_kwargs["extra_claims"]["user_id"] == USER_CONTEXT["user_id"]
    assert call_kwargs["requested_scopes"] == USER_CONTEXT["scopes"]
    assert "scope" not in call_kwargs["extra_claims"]
    assert call_kwargs["extra_claims"]["groups"] == USER_CONTEXT["groups"]


@pytest.mark.parametrize(
    ("request_data", "expected_scope"),
    [
        (
            TokenGenerateRequest(expiresInHours=8),
            "servers-write mcp-proxy-ops a2a-proxy-ops",
        ),
        (
            TokenGenerateRequest(expiresInHours=8, tokenPurpose=TokenPurpose.INTERACTIVE),
            "servers-write mcp-proxy-ops a2a-proxy-ops",
        ),
        (
            TokenGenerateRequest(expiresInHours=8, tokenPurpose=TokenPurpose.AGENT),
            "mcp-proxy-ops a2a-proxy-ops",
        ),
    ],
)
async def test_generate_user_token_applies_client_scope_ceiling(
    request_data: TokenGenerateRequest,
    expected_scope: str,
) -> None:
    result = await generate_user_token(request_data, CEILING_TEST_USER_CONTEXT)

    assert result.tokenData.scope == expected_scope
    assert result.requestedScopes == CEILING_TEST_SCOPES


async def test_generate_agent_token_scope_claim_matches_response() -> None:
    request_data = TokenGenerateRequest(
        expiresInHours=8,
        requestedScopes=CEILING_TEST_SCOPES,
        tokenPurpose=TokenPurpose.AGENT,
    )

    result = await generate_user_token(request_data, CEILING_TEST_USER_CONTEXT)
    claims = verify_managed_agent_token(settings.jwt_token_config, result.tokenData.accessToken)

    assert claims["scope"] == "mcp-proxy-ops a2a-proxy-ops"
    assert claims["scope"] == result.tokenData.scope
    assert result.requestedScopes == CEILING_TEST_SCOPES


def test_token_generate_request_rejects_unknown_purpose() -> None:
    with pytest.raises(ValidationError):
        TokenGenerateRequest(expiresInHours=8, tokenPurpose="unsupported")
