"""Shared ``redirect_uri`` validation for OAuth flows (DCR registration + authorization).

Both ``auth-server`` (first-party ``/oauth2/*`` flow) and ``registry`` (per-server downstream
direct-connect flow) accept client-supplied ``redirect_uri`` values that later become 302 redirect
sinks. The same anti-open-redirect / anti-SSRF rules must apply in both places, so the single source
of truth lives here in ``registry-pkgs`` where both workspaces can import it.

Two validation phases with different rules:

* **Registration** (``validate_registration_redirect_uri``): structural allow/deny applied once when a
  client registers a ``redirect_uri``. Rejects non-loopback ``http://``, ``https://`` to
  private/loopback/link-local/unspecified IPs, and any URI carrying a fragment.
* **Authorization** (``redirect_uri_matches``): compares a request-time ``redirect_uri`` against a
  previously registered one. Exact match for normal URIs; loopback URIs match on scheme+host+path and
  ignore the port (RFC 8252 §7.3 — native apps bind an ephemeral loopback port per launch).
"""

import ipaddress
from urllib.parse import urlsplit

LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_host(host: str | None) -> bool:
    """Return True for ``localhost`` / ``127.0.0.1`` / ``[::1]`` (host as returned by ``.hostname``)."""
    if not host:
        return False
    return host.lower() in LOOPBACK_HOSTNAMES


def _is_blocked_https_ip(host: str) -> bool:
    """True if ``host`` is a literal IP in a range that must never be an https redirect target.

    Blocks RFC-1918 private ranges, 169.254.0.0/16 link-local, 127.0.0.0/8 loopback, and 0.0.0.0.
    Non-IP hostnames are not blocked here (they cannot be range-checked at registration time).
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified


def validate_registration_redirect_uri(redirect_uri: str) -> None:
    """Validate a ``redirect_uri`` at client-registration time. Raise ``ValueError`` if disallowed.

    Rules:
      * must be an absolute ``http``/``https`` URL with a host;
      * must not carry a fragment (``#...``);
      * ``http`` is allowed only for loopback hosts;
      * ``https`` is rejected when the host is a private / loopback / link-local / unspecified IP.
    """
    parts = urlsplit(redirect_uri)
    scheme = parts.scheme.lower()

    if scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("redirect_uri must be an absolute http(s) URL")

    if parts.fragment:
        raise ValueError("redirect_uri must not contain a fragment")

    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("redirect_uri must include a host")

    if scheme == "http" and not is_loopback_host(host):
        raise ValueError("http redirect_uri is only allowed for loopback hosts")

    if scheme == "https" and _is_blocked_https_ip(host):
        raise ValueError("https redirect_uri must not target a private, loopback, or link-local address")


def redirect_uri_matches(received: str, registered: str) -> bool:
    """Return True if a request-time ``received`` redirect_uri matches a ``registered`` one.

    Non-loopback: exact string match (port included). Loopback: match scheme + host + path, ignore
    the port so a native app's ephemeral loopback port does not break the match (RFC 8252 §7.3).
    """
    registered_parts = urlsplit(registered)

    if not is_loopback_host(registered_parts.hostname):
        return received == registered

    received_parts = urlsplit(received)
    return (
        is_loopback_host(received_parts.hostname)
        and received_parts.scheme.lower() == registered_parts.scheme.lower()
        and (received_parts.hostname or "").lower() == (registered_parts.hostname or "").lower()
        and received_parts.path == registered_parts.path
    )
