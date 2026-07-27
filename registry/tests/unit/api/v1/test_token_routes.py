"""Tests for user-vended managed-agent token generation."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from registry.api.v1.token_routes import generate_user_token
from registry.core.config import settings
from registry.schemas.common_api_schemas import TokenGenerateRequest
from registry.schemas.enums import TokenPurpose
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID

USER_CONTEXT = {
    "username": "alice",
    "user_id": "507f1f77bcf86cd799439011",
    "scopes": ["mcp:read", "mcp:execute"],
    "groups": ["developers"],
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
        "registry.api.v1.token_routes.mint_managed_agent_token",
        return_value="signed-token",
    ) as mint_token:
        result = await generate_user_token(request_data, USER_CONTEXT)

    assert result.tokenData.accessToken == "signed-token"
    assert result.requestedScopes == USER_CONTEXT["scopes"]
    mint_token.assert_called_once()
    call_kwargs = mint_token.call_args.kwargs
    assert call_kwargs["client_id"] == expected_client_id
    assert call_kwargs["expires_in_seconds"] == 8 * 3600
    assert call_kwargs["extra_claims"]["user_id"] == USER_CONTEXT["user_id"]
    assert call_kwargs["extra_claims"]["scope"] == " ".join(USER_CONTEXT["scopes"])
    assert call_kwargs["extra_claims"]["groups"] == USER_CONTEXT["groups"]


def test_token_generate_request_rejects_unknown_purpose() -> None:
    with pytest.raises(ValidationError):
        TokenGenerateRequest(expiresInHours=8, tokenPurpose="unsupported")
