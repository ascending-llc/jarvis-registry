"""Policy helpers for user-vended managed-agent tokens."""

from registry_pkgs.core.config import INTERACTIVE_TOKEN_CLIENT_ID

from ..schemas.enums import TokenPurpose

INTERACTIVE_CLIENT_ID = INTERACTIVE_TOKEN_CLIENT_ID


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


def is_direct_connect_a2a_client(
    client_id: str,
    headless_agent_client_id: str,
) -> bool:
    """Return whether a client ID may use the direct-connect A2A proxy routes.

    Both allowed client IDs are issued only through the authenticated Registry token-vending
    endpoint, not through Dynamic Client Registration. Other clients are denied because the
    direct-connect A2A routes do not have a per-agent consent fallback.
    """
    return client_id in {headless_agent_client_id, INTERACTIVE_CLIENT_ID}
