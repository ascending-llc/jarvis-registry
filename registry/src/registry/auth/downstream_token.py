"""Downstream MCP confirmation tokens.

A *downstream confirmation token* is a short-lived, registry-signed JWT handed to an MCP client
once it has completed the downstream OAuth flow (Layer B: registry-as-AS) for a specific
``(user_id, server_path)`` pair. The client presents it as a Bearer token on its next
``initialize`` attempt; ``UnifiedAuthMiddleware`` accepts it for that one direct-connect route.

The ``iss`` claim encodes both ``user_id`` and ``server_path`` so the verifier can cross-check
them against the request URL, preventing a token minted for one user/server from being replayed
on another. Minting and verification live together here so the two stay in lock-step.
"""

import logging
import re

from registry_pkgs.core.config import JwtTokenConfig
from registry_pkgs.core.downstream_oauth import downstream_mcp_issuer
from registry_pkgs.core.jwt_utils import build_jwt_payload, decode_jwt, encode_jwt

from ..core.config import settings

logger = logging.getLogger(__name__)

DIRECT_CONNECT_RE = re.compile(r"^/proxy/server/([^/]+)/(.+)$")

TOKEN_CLASS_DOWNSTREAM_MCP = "downstream_mcp"
DOWNSTREAM_MCP_TOKEN_TTL_SECONDS = 300


def mint_downstream_mcp_token(config: JwtTokenConfig, *, user_id: str, server_path: str) -> str:
    """Mint a downstream confirmation token bound to a single ``(user_id, server_path)``."""
    payload = build_jwt_payload(
        subject=user_id,
        issuer=downstream_mcp_issuer(config.jwt_issuer, user_id, server_path),
        audience=config.managed_agents_audience,
        expires_in_seconds=DOWNSTREAM_MCP_TOKEN_TTL_SECONDS,
        extra_claims={"token_class": TOKEN_CLASS_DOWNSTREAM_MCP},
    )
    return encode_jwt(payload, config.jwt_private_key, kid=config.jwt_self_signed_kid)


def verify_downstream_mcp_token(token: str, path: str, jwt_public_key: str) -> str | None:
    """Verify a downstream confirmation token for a direct-connect request.

    Returns the bound ``user_id`` if the token is valid for *this* request path, else ``None``.

    The token's ``iss`` encodes ``(user_id, server_path)``. We reconstruct the expected issuer
    from the request URL and require an exact match during decode, so a token minted for user A's
    GitHub is cryptographically rejected on user B's GitHub route or on any other server — the
    cross-user / cross-server confusion guard collapses into the standard ``iss`` check.
    """
    match = DIRECT_CONNECT_RE.match(path)
    if match is None:
        return None
    url_user_id, url_server_path = match.group(1), match.group(2)

    expected_iss = downstream_mcp_issuer(settings.jwt_issuer, url_user_id, url_server_path)

    try:
        claims = decode_jwt(
            token,
            jwt_public_key,
            issuer=expected_iss,
            audience=settings.jwt_audience_managed_agents,
        )
    except Exception as e:  # noqa: BLE001 — any verification failure means "not a usable token"
        logger.debug(f"downstream confirmation token rejected: {e}")
        return None

    if claims.get("token_class") != TOKEN_CLASS_DOWNSTREAM_MCP:
        logger.debug("downstream confirmation token rejected: wrong token_class")
        return None

    return claims.get("sub")
