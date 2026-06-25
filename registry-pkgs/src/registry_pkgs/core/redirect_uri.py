"""Shared ``redirect_uri`` validation for OAuth flows (DCR registration + authorization).

Both ``auth-server`` (first-party ``/oauth2/*`` flow) and ``registry`` (per-server downstream
direct-connect flow) accept client-supplied ``redirect_uri`` values that later become 302 redirect
sinks. The same anti-open-redirect / anti-SSRF rules must apply in both places, so the single source
of truth lives here in ``registry-pkgs`` where both workspaces can import it.

Two validation phases with different rules:

* **Registration** (``validate_registration_redirect_uri``): structural allow/deny applied once when a
  client registers a ``redirect_uri``. Rejects dangerous schemes (``javascript:``, ``data:`` …),
  non-loopback ``http://``, ``https://`` to private/loopback/link-local/unspecified IPs, and any URI
  carrying a fragment. Native-app private-use schemes (RFC 8252 §7.1, e.g. ``vscode://``) are allowed.
* **Authorization** (``redirect_uri_matches``): compares a request-time ``redirect_uri`` against a
  previously registered one. Exact match for normal URIs; loopback URIs match on scheme+host+path and
  ignore the port (RFC 8252 §7.3 — native apps bind an ephemeral loopback port per launch).
"""

import ipaddress
from urllib.parse import urlsplit

LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

BLOCKED_REDIRECT_SCHEMES = frozenset({"javascript", "data", "vbscript", "file", "blob", "about"})


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


def _validate_web_redirect_host(scheme: str, hostname: str | None) -> None:
    """Vet the host of an ``http`` / ``https`` redirect_uri. Raise ``ValueError`` if disallowed.

    PASS: ``https`` to any public host; ``http`` to a loopback host.
    FAIL: missing host; plaintext ``http`` to a non-loopback host; ``https`` to an internal IP.
    """
    host = (hostname or "").lower()

    # No host at all (e.g. "https:///cb"): not a usable callback target.
    if not host:
        raise ValueError("http(s) redirect_uri must include a host")

    if scheme == "http" and not is_loopback_host(host):
        raise ValueError("http redirect_uri is only allowed for loopback hosts (localhost / 127.0.0.1 / [::1])")

    if scheme == "https" and _is_blocked_https_ip(host):
        raise ValueError("https redirect_uri must not target a private, loopback, or link-local address")


def validate_registration_redirect_uri(redirect_uri: str) -> None:
    """Validate a client-supplied ``redirect_uri`` at DCR registration time.

    Returns ``None`` when the URI is acceptable; raises ``ValueError`` (with a human-readable reason)
    otherwise. The URI later becomes a 302 redirect sink that carries the authorization code, so this
    is the anti-open-redirect / anti-SSRF / anti-phishing gate.

    A redirect_uri is ACCEPTED only if it is one of the three forms a real client legitimately uses
    (RFC 6749 §3.1.2 for web apps, RFC 8252 §7 for native apps):

      1. ``https://<public-host>/...``     web / claimed-https callback
      2. ``http://<loopback>/...``         native app loopback listener (localhost / 127.0.0.1 / [::1])
      3. ``<private-use-scheme>:/...``     native app custom scheme, e.g. ``vscode://`` (Cline / VS Code)

    Rules are applied in this order — first match wins:

      | check                                              | result                          |
      |----------------------------------------------------|---------------------------------|
      | no scheme (not an absolute URI)                    | FAIL (RFC 6749 §3.1.2)          |
      | contains a ``#fragment``                           | FAIL (RFC 6749 §3.1.2)          |
      | dangerous scheme: javascript/data/file/...         | FAIL (would execute in browser) |
      | any non-http(s) scheme                             | PASS — native private-use scheme|
      | http(s): host vetted by ``_validate_web_redirect_host`` | PASS or FAIL               |
    """
    parts = urlsplit(redirect_uri)
    scheme = parts.scheme.lower()

    if not scheme:
        raise ValueError("redirect_uri must be an absolute URI (no scheme found)")
    if parts.fragment:
        raise ValueError("redirect_uri must not contain a fragment (#...)")
    if scheme in BLOCKED_REDIRECT_SCHEMES:
        raise ValueError(f"redirect_uri scheme '{scheme}:' is not allowed")

    # --- Native-app private-use scheme (RFC 8252 §7.1), e.g. vscode:// ---
    # Hands off to a local app, never to a network host, so the host rules below do not apply.
    # Cross-client abuse is prevented by the exact registered==received match at authorization time.
    if scheme not in {"http", "https"}:
        return

    # --- http / https: must point at a vetted host ---
    _validate_web_redirect_host(scheme, parts.hostname)


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
