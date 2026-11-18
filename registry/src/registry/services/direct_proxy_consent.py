"""Consent policy and pending state for direct MCP connections."""

import secrets

from starlette.concurrency import run_in_threadpool

from registry.auth.dependencies import UserContextDict
from registry.core.config import settings
from registry.core.proxy_call import CONSENT_EXEMPT_METHODS, ProxyCall
from registry.services.generated_token_policy import is_consent_exempt
from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore


async def get_proxy_consent_url(
    call: ProxyCall,
    *,
    auth_context: UserContextDict,
    server_path: str,
    consent_store: ConsentStore,
    pending_store: PendingConsentStore,
) -> str | None:
    """Return a pending consent URL, or None when this call may proceed.

    Redis failures propagate to the route's protocol error boundary. Synchronous
    stores run in a thread pool so a slow Redis does not block other requests.
    """
    user_id = auth_context["user_id"]
    client_id = auth_context["client_id"]
    if call.method in CONSENT_EXEMPT_METHODS or is_consent_exempt(client_id, settings.headless_agent_client_id):
        return None
    if await run_in_threadpool(consent_store.has_server_consent, user_id, client_id, server_path):
        return None

    nonce = secrets.token_urlsafe(32)
    await run_in_threadpool(
        pending_store.save, nonce, {"user_id": user_id, "client_id": client_id, "server_path": server_path}
    )
    return f"{settings.registry_client_url}/consent/server?nonce={nonce}"
