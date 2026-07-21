"""
Manual end-to-end test of the OAuth 2.0 Device Authorization Grant flow (AS-1726).

Drives DCR registration (public + confidential clients), device authorization,
browser-based IdP login + Consent Type 1, and token-endpoint polling against a
locally running `auth-server` (docker compose, see docker-compose.no-db.yml).

No mainstream MCP client (Claude Code CLI, VS Code Copilot) currently drives
device flow against a custom MCP server, so this script plays that client role
by hand. It intentionally only talks to `auth-server` over HTTP and only reads
JWT-decoding helpers from `registry_pkgs` — the same public surface a real,
external device-flow client would have.

Runs the full flow twice:
  - "Mode 1" client:      DCR with token_endpoint_auth_method=none (public client)
  - "Mode 2 sub-case B":  DCR with token_endpoint_auth_method=client_secret_post (confidential client)

Both are DCR-registered clients (never `registry_app_name`, the registry's own
first-party SPA client, which never uses device flow) and both go through
Consent Type 1 (client-id consent) since neither is the registry's own client.

Usage:
    uv run python scripts/test_device_flow.py
"""

from __future__ import annotations

import json
import os
import time
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Use your own `.env` path or ask Kent for his.
load_dotenv(Path(__file__).resolve().parents[1] / ".env.no-db")

from registry_pkgs.core.jwt_utils import (
    DecodeError,
    InvalidTokenError,
    decode_jwt_with_jwk,
    find_matching_jwk,
    get_token_kid,
)

# ==================== Configuration ====================
AUTH_SERVER_EXTERNAL_URL = os.environ.get("AUTH_SERVER_EXTERNAL_URL", "http://localhost:8888")
JWT_AUDIENCE_MANAGED_AGENTS = os.environ.get("JWT_AUDIENCE_MANAGED_AGENTS", "jarvis-managed-agents")

# DCR requires at least one redirect_uri, but device flow never redirects to it.
DCR_REDIRECT_URI = "https://example.com/callback"

# RFC 8628 device-code grant URN.
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

REQUEST_TIMEOUT_SECONDS = 10
SLOW_DOWN_BACKOFF_SECONDS = 5  # RFC 8628 §3.5


def _print_header(title: str) -> None:
    print(f"\n{'=' * 80}\n{title}\n{'=' * 80}")


def _print_json(label: str, data: dict) -> None:
    print(f"{label}:\n{json.dumps(data, indent=2, default=str)}")


def fetch_as_metadata(client: httpx.Client) -> dict:
    _print_header("Authorization Server Metadata")
    response = client.get(f"{AUTH_SERVER_EXTERNAL_URL}/.well-known/oauth-authorization-server")
    response.raise_for_status()
    metadata = response.json()
    print(f"token_endpoint:                {metadata['token_endpoint']}")
    print(f"device_authorization_endpoint: {metadata['device_authorization_endpoint']}")
    print(f"registration_endpoint:         {metadata['registration_endpoint']}")
    print(f"jwks_uri:                      {metadata['jwks_uri']}")
    return metadata


def register_client(
    client: httpx.Client, registration_endpoint: str, *, label: str, token_endpoint_auth_method: str
) -> dict:
    _print_header(f"DCR Registration — {label} (token_endpoint_auth_method={token_endpoint_auth_method!r})")
    payload = {
        "client_name": f"test-device-flow-client ({label})",
        "redirect_uris": [DCR_REDIRECT_URI],
        "token_endpoint_auth_method": token_endpoint_auth_method,
    }
    response = client.post(registration_endpoint, json=payload)
    response.raise_for_status()
    data = response.json()
    print(f"client_id:                  {data['client_id']}")
    print(f"client_secret:              {data.get('client_secret')}")
    print(f"grant_types:                {data['grant_types']}")
    print(f"token_endpoint_auth_method: {data['token_endpoint_auth_method']}")
    return data


def start_device_flow(client: httpx.Client, device_authorization_endpoint: str, *, client_id: str) -> dict:
    _print_header("Device Authorization Request")
    response = client.post(device_authorization_endpoint, data={"client_id": client_id})
    response.raise_for_status()
    data = response.json()
    _print_json("Response body", data)
    print(f"\nuser_code: {data['user_code']}")
    print(f"Opening {data['verification_uri']} in your default browser...")
    webbrowser.open(data["verification_uri"])
    return data


def poll_for_token(
    client: httpx.Client,
    token_endpoint: str,
    *,
    device_code: str,
    client_id: str,
    client_secret: str | None,
    interval_seconds: int,
    expires_in_seconds: int,
) -> dict:
    _print_header("Polling Token Endpoint")
    deadline = time.monotonic() + expires_in_seconds
    while True:
        time.sleep(interval_seconds)
        payload = {"grant_type": DEVICE_CODE_GRANT_TYPE, "device_code": device_code, "client_id": client_id}
        if client_secret is not None:
            payload["client_secret"] = client_secret

        response = client.post(token_endpoint, data=payload)
        body = response.json()
        _print_json(f"[poll] HTTP {response.status_code}", body)

        if response.status_code == 200:
            return body

        error = body.get("error")
        if error == "authorization_pending":
            if time.monotonic() > deadline:
                raise TimeoutError("device_code expired while waiting for user approval")
            continue
        if error == "slow_down":
            interval_seconds += SLOW_DOWN_BACKOFF_SECONDS
            continue

        raise RuntimeError(f"device flow failed: error={error} description={body.get('error_description')}")


def decode_and_print_tokens(token_response: dict, jwks: dict, issuer: str) -> None:
    _print_header("Decoded Tokens")

    access_token = token_response["access_token"]
    access_jwk = find_matching_jwk(jwks, get_token_kid(access_token))
    access_claims = decode_jwt_with_jwk(
        access_token,
        access_jwk,
        algorithms=["RS256"],
        issuer=issuer,
        audience=JWT_AUDIENCE_MANAGED_AGENTS,
    )
    _print_json("access_token claims", access_claims)

    refresh_token = token_response.get("refresh_token")
    if not refresh_token:
        return

    try:
        refresh_jwk = find_matching_jwk(jwks, get_token_kid(refresh_token))
        refresh_claims = decode_jwt_with_jwk(
            refresh_token,
            refresh_jwk,
            algorithms=["RS256"],
            issuer=issuer,
            audience=JWT_AUDIENCE_MANAGED_AGENTS,
        )
        _print_json("refresh_token claims", refresh_claims)
    except (DecodeError, InvalidTokenError):
        print(f"refresh_token (opaque, not a JWT): {refresh_token}")


def run_device_flow(client: httpx.Client, metadata: dict, jwks: dict, *, label: str, dcr_client: dict) -> None:
    _print_header(f"Device Flow — {label}")
    device_data = start_device_flow(
        client, metadata["device_authorization_endpoint"], client_id=dcr_client["client_id"]
    )
    token_response = poll_for_token(
        client,
        metadata["token_endpoint"],
        device_code=device_data["device_code"],
        client_id=dcr_client["client_id"],
        client_secret=dcr_client.get("client_secret"),
        interval_seconds=device_data["interval"],
        expires_in_seconds=device_data["expires_in"],
    )
    _print_json("Token response", token_response)
    decode_and_print_tokens(token_response, jwks, metadata["issuer"])


def main() -> None:
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        metadata = fetch_as_metadata(client)
        jwks = client.get(metadata["jwks_uri"]).json()

        public_client = register_client(
            client,
            metadata["registration_endpoint"],
            label="Mode 1 / public",
            token_endpoint_auth_method="none",
        )
        confidential_client = register_client(
            client,
            metadata["registration_endpoint"],
            label="Mode 2 sub-case B / confidential",
            token_endpoint_auth_method="client_secret_post",
        )

        run_device_flow(
            client,
            metadata,
            jwks,
            label="Mode 1 (public client, token_endpoint_auth_method=none)",
            dcr_client=public_client,
        )
        run_device_flow(
            client,
            metadata,
            jwks,
            label="Mode 2 sub-case B (confidential client, token_endpoint_auth_method=client_secret_post)",
            dcr_client=confidential_client,
        )


if __name__ == "__main__":
    main()
