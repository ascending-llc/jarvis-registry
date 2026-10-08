"""MCP OAuth discovery (MCP authorization spec 2025-06-18 / 2025-11-25).

This is the only place in the codebase that performs MCP OAuth discovery. It follows the spec's
chain using the ``mcp`` SDK's helpers:

1. Read the ``WWW-Authenticate`` challenge of an unauthenticated request (or take a supplied one).
2. Find the RFC 9728 protected-resource metadata (PRM): header URL, path-based, then root.
3. Validate the PRM ``resource`` against the server URL (RFC 9728 §3.3).
4. Find the RFC 8414 authorization-server metadata of ``authorization_servers[0]`` (or, with no
   PRM, the legacy 2025-03-26 origin-based location).
5. Validate the metadata ``issuer`` (RFC 8414 §3.3).
6. Choose scopes per the spec's scope selection strategy.

The SDK helpers live in ``mcp.client.auth.utils``, which is not part of the package's public API.
They are imported only here, and ``test_discovery.py`` imports every one of them so an SDK upgrade
that renames one fails CI rather than production.
"""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_field_from_www_auth,
    get_client_metadata_scopes,
)
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from pydantic import BaseModel, ValidationError

from .errors import OAuthDiscoveryError

logger = logging.getLogger(__name__)

# Per-request limit. Discovery runs on interactive paths, so a hung provider must not stall a request.
DISCOVERY_TIMEOUT_SECONDS = 10.0
# Limit for the whole chain (challenge probe, up to 3 PRM candidates, the SDK's AS candidates).
DISCOVERY_TOTAL_TIMEOUT_SECONDS = 15.0

_PROBE_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


class DiscoveryResult(BaseModel):
    """Outcome of a successful MCP OAuth discovery."""

    server_url: str
    resource: str | None = None
    protected_resource_metadata: dict[str, Any] | None = None
    authorization_server_metadata: dict[str, Any]
    issuer: str
    scope: str | None = None


def _strip_trailing_slash(url: str) -> str:
    return url.rstrip("/")


def _validate_prm_resource(resource: str, server_url: str) -> None:
    """RFC 9728 §3.3: the PRM must describe this server (same origin, path equal or a path prefix)."""
    res = urlparse(resource)
    srv = urlparse(server_url)
    if res.scheme.lower() != srv.scheme.lower() or res.netloc.lower() != srv.netloc.lower():
        raise OAuthDiscoveryError(f"Protected resource metadata names resource {resource!r}, not {server_url!r}")
    res_path = res.path.rstrip("/")
    srv_path = srv.path.rstrip("/")
    if srv_path != res_path and not srv_path.startswith(res_path + "/"):
        raise OAuthDiscoveryError(f"Protected resource metadata names resource {resource!r}, not {server_url!r}")


async def _fetch_json(http_client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    """GET a well-known document; None for a non-200 or a body that isn't a JSON object."""
    try:
        response = await http_client.get(url, timeout=DISCOVERY_TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        raise OAuthDiscoveryError(f"Failed to fetch OAuth metadata from {url}: {e}") from e
    if response.status_code != 200:
        logger.debug("OAuth discovery candidate %s answered %s", url, response.status_code)
        return None
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.debug("OAuth discovery candidate %s returned a non-JSON body", url)
        return None
    return body if isinstance(body, dict) else None


async def _probe_challenge(http_client: httpx.AsyncClient, server_url: str) -> str | None:
    """Send an unauthenticated request and return the 401's WWW-Authenticate header, if any."""
    try:
        # Stream so an unauthenticated server that answers with an open SSE stream can't hang us.
        async with http_client.stream(
            "POST", server_url, content=b"{}", headers=_PROBE_HEADERS, timeout=DISCOVERY_TIMEOUT_SECONDS
        ) as response:
            if response.status_code != 401:
                logger.debug("OAuth discovery probe of %s answered %s", server_url, response.status_code)
                return None
            return response.headers.get("WWW-Authenticate")
    except httpx.HTTPError as e:
        raise OAuthDiscoveryError(f"Failed to probe {server_url} for an OAuth challenge: {e}") from e


async def _discover(server_url: str, http_client: httpx.AsyncClient, www_authenticate: str | None) -> DiscoveryResult:
    if www_authenticate is None:
        www_authenticate = await _probe_challenge(http_client, server_url)

    # The SDK helper reads the header from a Response.
    challenge = httpx.Response(401, headers={"WWW-Authenticate": www_authenticate} if www_authenticate else {})
    resource_metadata_url = extract_field_from_www_auth(challenge, "resource_metadata")
    www_auth_scope = extract_field_from_www_auth(challenge, "scope")

    prm: ProtectedResourceMetadata | None = None
    prm_raw: dict[str, Any] | None = None
    for url in build_protected_resource_metadata_discovery_urls(resource_metadata_url, server_url):
        body = await _fetch_json(http_client, url)
        if body is None:
            continue
        try:
            prm = ProtectedResourceMetadata.model_validate(body)
        except ValidationError:
            logger.debug("OAuth discovery candidate %s is not valid protected resource metadata", url)
            continue
        prm_raw = body
        break

    resource: str | None = None
    as_url: str | None = None
    if prm is not None and prm_raw is not None:
        resource = str(prm_raw["resource"])
        _validate_prm_resource(resource, server_url)
        as_url = str(prm_raw["authorization_servers"][0])

    as_metadata: OAuthMetadata | None = None
    as_raw: dict[str, Any] | None = None
    for url in build_oauth_authorization_server_metadata_discovery_urls(as_url, server_url):
        body = await _fetch_json(http_client, url)
        if body is None:
            continue
        try:
            as_metadata = OAuthMetadata.model_validate(body)
        except ValidationError:
            logger.debug("OAuth discovery candidate %s is not valid authorization server metadata", url)
            continue
        as_raw = body
        break

    if as_metadata is None or as_raw is None:
        raise OAuthDiscoveryError(f"No authorization server metadata found for {server_url}")

    issuer = str(as_raw["issuer"])
    if as_url is not None:
        if _strip_trailing_slash(issuer) != _strip_trailing_slash(as_url):
            raise OAuthDiscoveryError(f"Authorization server metadata issuer {issuer!r} does not match {as_url!r}")
    else:
        origin = f"{urlparse(server_url).scheme}://{urlparse(server_url).netloc}"
        if _strip_trailing_slash(issuer) != origin:
            logger.warning(
                "Legacy OAuth discovery for %s: issuer %r differs from the server origin; accepting it",
                server_url,
                issuer,
            )

    return DiscoveryResult(
        server_url=server_url,
        resource=resource,
        protected_resource_metadata=prm_raw,
        authorization_server_metadata=as_raw,
        issuer=issuer,
        scope=get_client_metadata_scopes(www_auth_scope, prm, as_metadata),
    )


async def discover_mcp_oauth(
    server_url: str,
    *,
    http_client: httpx.AsyncClient,
    www_authenticate: str | None = None,
) -> DiscoveryResult:
    """Run MCP OAuth discovery for ``server_url``.

    Args:
        server_url: The MCP server endpoint (``config.url``).
        http_client: Client used for every request; discovery does no other network I/O.
        www_authenticate: A ``WWW-Authenticate`` value already received from the server. When
            None, an unauthenticated probe request obtains it.

    Raises:
        OAuthDiscoveryError: On any transport error, validation failure, missing authorization
            server metadata, or when the chain exceeds ``DISCOVERY_TOTAL_TIMEOUT_SECONDS``.
    """
    try:
        async with asyncio.timeout(DISCOVERY_TOTAL_TIMEOUT_SECONDS):
            return await _discover(server_url, http_client, www_authenticate)
    except TimeoutError as e:
        raise OAuthDiscoveryError(
            f"OAuth discovery for {server_url} exceeded {DISCOVERY_TOTAL_TIMEOUT_SECONDS}s"
        ) from e
