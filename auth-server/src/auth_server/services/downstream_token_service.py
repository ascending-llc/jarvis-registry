"""Downstream token check service for the auth server.

Decides whether a user already holds usable downstream MCP tokens for a given server, so the
protected resource metadata can point the MCP client at the right authorization server: the
standard registry issuer when tokens exist, or the per-server downstream issuer when they don't.

``ExtendedMCPServer``, ``Token``, and ``TokenType`` are imported from ``registry-pkgs`` — within
the allowed dependency direction (``auth-server`` → ``registry-pkgs``).
"""

import logging
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId

from registry_pkgs.models import ExtendedMCPServer, Token, TokenType

logger = logging.getLogger(__name__)

_EXPIRY_BUFFER = timedelta(seconds=3)


class DownstreamTokenCheckService:
    """Checks MongoDB for valid downstream MCP access/refresh tokens."""

    async def has_valid_downstream_token(self, user_id: str, server_path: str) -> bool:
        """Return True if the user has a non-expired access or refresh token for the server."""
        server = await ExtendedMCPServer.find_one({"path": f"/{server_path}"})
        if not server:
            return False

        service_name = server.serverName
        user_obj_id = PydanticObjectId(user_id)
        cutoff = datetime.now(UTC) + _EXPIRY_BUFFER

        # Single round-trip: a non-expired access OR refresh token for this user/server.
        token = await Token.find_one(
            {
                "userId": user_obj_id,
                "expiresAt": {"$gt": cutoff},
                "$or": [
                    {"type": TokenType.MCP_OAUTH_ACCESS.value, "identifier": f"mcp:{service_name}"},
                    {"type": TokenType.MCP_OAUTH_REFRESH.value, "identifier": f"mcp:{service_name}:refresh"},
                ],
            }
        )
        return token is not None
