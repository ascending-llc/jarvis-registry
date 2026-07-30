"""Tests for generated-token purpose and consent policy."""

from typing import cast

import pytest

from registry.schemas.enums import TokenPurpose
from registry.services.generated_token_policy import (
    INTERACTIVE_CLIENT_ID,
    is_consent_exempt,
    resolve_generated_token_client_id,
)

HEADLESS_CLIENT_ID = "test-headless-agent"


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
