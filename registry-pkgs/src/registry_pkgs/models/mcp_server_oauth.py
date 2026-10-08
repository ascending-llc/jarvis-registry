"""Registry-owned OAuth state stored on ``mcpservers`` documents as top-level ``registryOAuth``.

Registry keeps its OAuth identity separate from Jarvis Chat: one client per server (registered
through DCR, or none for static-client servers) plus the discovery result both are bound to. The
field lives at the top level because Chat replaces the whole ``config`` sub-document on edit.

These are plain Pydantic models, not Beanie Documents. ``MCPOAuthService`` is their only writer.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RegistryOAuthClient(BaseModel):
    """One OAuth client registered by Registry for one server."""

    clientId: str
    # Encrypted with encrypt_value.
    clientSecret: str | None = None
    redirectUri: str
    tokenEndpointAuthMethod: str
    # The scope Registry sent at registration, never the registration response's scope, which a
    # provider may reorder, trim or extend.
    scope: str | None = None
    registeredAt: datetime
    # RFC 7591 client_secret_expires_at; None when absent or 0 (never expires).
    clientSecretExpiresAt: datetime | None = None


class RegistryOAuthState(BaseModel):
    """Discovery result and client for one server, plus the server URL both are bound to."""

    # uuid4; the compare-and-swap token for every write.
    revision: str
    # config.url at discovery time.
    serverUrl: str
    resource: str | None = None
    # None only for a static-client server whose discovery failed.
    issuer: str | None = None
    protectedResourceMetadata: dict[str, Any] | None = None
    # None only for a static-client server whose discovery failed.
    authorizationServerMetadata: dict[str, Any] | None = None
    scope: str | None = None
    discoveredAt: datetime
    # None for static-client servers, and for DCR servers before first registration.
    client: RegistryOAuthClient | None = None

    def is_bound_to(self, server_url: str) -> bool:
        """Whether this state still describes ``server_url``.

        A stale binding (the URL was edited, or a login that started before an edit wrote late) is
        treated like an absent state: its discovery data and its client are both discarded, because
        the client was registered with that discovery's scopes.
        """
        return self.serverUrl == server_url
