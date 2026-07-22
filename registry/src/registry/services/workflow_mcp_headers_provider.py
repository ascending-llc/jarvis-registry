from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows.types import McpConsentRequiredError

from ..auth.dependencies import UserContextDict
from ..mcpgw.tools.utils import build_authenticated_headers

if TYPE_CHECKING:
    from ...services.oauth.oauth_service import MCPOAuthService

logger = logging.getLogger(__name__)


class McpHeadersProvider:
    """Build per-call MCP auth headers for manually-registered servers.

    Use as the ``mcp_headers_provider`` callback for ``make_mcp_executor`` /
    ``build_executor_registry``.  This lives in the ``registry`` app (not
    ``registry-pkgs``) because it depends on the concrete ``MCPOAuthService``
    and ``build_authenticated_headers``.
    """

    def __init__(
        self,
        *,
        oauth_service: MCPOAuthService,
        consent_store: ConsentStore,
        pending_consent_store: PendingConsentStore,
        registry_client_url: str,
        redis_client: Any | None = None,
    ) -> None:
        self._oauth_service = oauth_service
        self._consent_store = consent_store
        self._pending_consent_store = pending_consent_store
        self._registry_client_url = registry_client_url.rstrip("/")
        self._redis_client = redis_client

    async def __call__(self, server: ExtendedMCPServer, auth_context: UserContextDict | None) -> dict[str, str]:
        if auth_context is None:
            raise ValueError("auth_context is required to build MCP headers")
        await self.authorize(server, auth_context)
        return await build_authenticated_headers(
            self._oauth_service,
            server,
            auth_context,
            redis_client=self._redis_client,
        )

    async def authorize(self, server: ExtendedMCPServer, auth_context: UserContextDict) -> None:
        """Require current client-to-server consent before any direct MCP call."""
        user_id = auth_context.get("user_id")
        client_id = auth_context.get("client_id")
        if not user_id or not client_id:
            raise ValueError("user_id and client_id are required to check MCP server consent")
        if not self._consent_store.has_server_consent(user_id, client_id, server.path):
            nonce = secrets.token_urlsafe(32)
            elicitation_id = str(uuid4())
            self._pending_consent_store.save(
                nonce,
                {
                    "user_id": user_id,
                    "client_id": client_id,
                    "server_path": server.path,
                    "elicitation_id": elicitation_id,
                },
            )
            raise McpConsentRequiredError(
                auth_url=f"{self._registry_client_url}/consent/server?nonce={nonce}",
                server_name=server.serverName,
                elicitation_id=elicitation_id,
            )


def make_mcp_headers_provider(
    *,
    oauth_service: MCPOAuthService,
    consent_store: ConsentStore,
    pending_consent_store: PendingConsentStore,
    registry_client_url: str,
    redis_client: Any | None = None,
) -> McpHeadersProvider:
    """Factory used by the DI container."""
    return McpHeadersProvider(
        oauth_service=oauth_service,
        consent_store=consent_store,
        pending_consent_store=pending_consent_store,
        registry_client_url=registry_client_url,
        redis_client=redis_client,
    )
