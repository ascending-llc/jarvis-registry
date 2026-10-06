"""Policy helpers for user-vended managed-agent tokens."""

from collections.abc import Iterable

from fastapi import HTTPException, status

from registry_pkgs.core.client_categories import get_client_max_scopes
from registry_pkgs.core.config import INTERACTIVE_TOKEN_CLIENT_ID, JwtTokenConfig
from registry_pkgs.core.scopes import map_groups_to_scopes

from ..constants import (
    GENERATED_TOKEN_EMPTY_REQUEST_DETAIL,
    GENERATED_TOKEN_NO_GRANTABLE_SCOPES_DETAIL,
    GENERATED_TOKEN_NO_USER_SCOPES_DETAIL,
    GENERATED_TOKEN_SCOPES_OUTSIDE_CEILING_DETAIL,
)
from ..schemas.enums import TokenPurpose

INTERACTIVE_CLIENT_ID = INTERACTIVE_TOKEN_CLIENT_ID

# Token-type labels as the token generation page shows them.
_TOKEN_PURPOSE_LABELS: dict[TokenPurpose, str] = {
    TokenPurpose.INTERACTIVE: "Interactive",
    TokenPurpose.AGENT: "Non-interactive agent",
}


def resolve_generated_token_client_id(
    token_purpose: TokenPurpose,
    headless_agent_client_id: str,
) -> str:
    """Resolve the managed-agent client ID for a requested token purpose."""
    if token_purpose == TokenPurpose.INTERACTIVE:
        return INTERACTIVE_CLIENT_ID

    if token_purpose == TokenPurpose.AGENT:
        return headless_agent_client_id

    raise ValueError(f"Unsupported token purpose: {token_purpose}")


def is_consent_exempt(
    client_id: str,
    headless_agent_client_id: str,
) -> bool:
    """Return whether a client ID is exempt from per-resource consent."""
    return client_id == headless_agent_client_id


def get_user_token_scopes(groups: list[str]) -> list[str]:
    """Return the scopes a user may put in a generated token.

    These come from the user's group mappings, not the session's scopes: the registry app's
    ceiling strips the proxy-ops scopes from the session, but managed-agent tokens need them.
    """
    return map_groups_to_scopes(groups)


def _format_scopes(scopes: Iterable[str]) -> str:
    return ", ".join(sorted(scopes))


def resolve_generated_token_scopes(
    requested_scopes: list[str] | None,
    available_scopes: list[str],
    token_purpose: TokenPurpose,
    config: JwtTokenConfig,
) -> list[str]:
    """Return the scopes to mint into a generated token, or raise HTTPException(4xx).

    ``requested_scopes`` is None for "use my current scopes", where ``available_scopes`` is narrowed
    silently to the token type's ceiling and only an empty result is an error. An explicit request
    is rejected instead if any scope would be dropped.
    """
    ceiling = get_client_max_scopes(
        resolve_generated_token_client_id(token_purpose, config.headless_agent_client_id),
        config,
    )
    purpose_label = _TOKEN_PURPOSE_LABELS[token_purpose]

    if requested_scopes is not None:
        if not requested_scopes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=GENERATED_TOKEN_EMPTY_REQUEST_DETAIL)

        unique_requested = list(dict.fromkeys(requested_scopes))

        available_set = set(available_scopes)
        exceeding = [scope for scope in unique_requested if scope not in available_set]
        if exceeding:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requested scopes exceed user permissions. Invalid scopes: {exceeding}",
            )

        outside_ceiling = [scope for scope in unique_requested if scope not in ceiling]
        if outside_ceiling:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=GENERATED_TOKEN_SCOPES_OUTSIDE_CEILING_DETAIL.format(
                    purpose=purpose_label,
                    rejected=_format_scopes(outside_ceiling),
                    allowed=_format_scopes(ceiling),
                ),
            )

        return unique_requested

    if not available_scopes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=GENERATED_TOKEN_NO_USER_SCOPES_DETAIL)

    granted = [scope for scope in dict.fromkeys(available_scopes) if scope in ceiling]
    if not granted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GENERATED_TOKEN_NO_GRANTABLE_SCOPES_DETAIL.format(
                purpose=purpose_label,
                allowed=_format_scopes(ceiling),
            ),
        )
    return granted
