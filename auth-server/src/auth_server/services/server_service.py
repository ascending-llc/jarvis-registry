"""
Server service for auth-server - handles user lookups of ExtendedMCPServer from MongoDB.
"""

import logging
from typing import Any

from pydantic import BaseModel

from registry_pkgs.models import ExtendedMCPServer

logger = logging.getLogger(__name__)


class _RequiresOAuthProjection(BaseModel):
    config: dict[str, Any]

    class Settings:
        projection = {"config.requiresOAuth": 1, "_id": 0}


class ServerService:
    """Service for server-related operations in auth-server."""

    def __init__(self) -> None:
        self._requires_oauth_cache: dict[str, bool] = {}

    async def requires_oauth(self, server_path: str) -> bool:
        """Return whether the server requires downstream OAuth.

        Args:
            server_path: Bare server path without leading slash (e.g. ``github``, ``agentcore/mcp/myserver``).

        Returns:
            Value of ``config.requiresOAuth``; False when the field is absent or the server does not exist.
        """
        normalized = f"/{server_path}"
        if normalized in self._requires_oauth_cache:
            return self._requires_oauth_cache[normalized]

        # MongoDB errors bubble up unhandled and become HTTP 500 in the route layer.
        doc = await ExtendedMCPServer.find_one({"path": normalized}, projection_model=_RequiresOAuthProjection)

        result = bool(doc.config.get("requiresOAuth", False)) if doc else False

        self._requires_oauth_cache[normalized] = result

        return result
